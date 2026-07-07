/*
 * Copyright (c) HiSilicon (Shanghai) Technologies Co., Ltd. 2023-2023. All rights reserved.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * 摔倒监测 + 毫米波雷达演示（智能头盔形态）：
 *   ICM20948 九轴姿态解算 + 摔倒检测 + 偏头转向灯；HLK-LD2450 雷达检测后方目标快速逼近；
 *   OLED 显示，蜂鸣器报警（摔倒优先），按键消音；三条 WS2812 灯带做灯效（见 led_fx_update）。
 * 接线：
 *   ICM20948/OLED（I2C1，模式2）：SCL -> GPIO16，SDA -> GPIO15，VCC -> 3.3V
 *   LD2450 雷达（UART2，模式2）：雷达TX -> GPIO7(UART2_RXD)，雷达RX -> GPIO8(UART2_TXD)，VCC -> 5V
 *   蜂鸣器：GPIO9（交通灯板 A9）；按键：GPIO13（板载USER）/GPIO14（交通灯板）
 *   WS2812 灯带 x3：右 DIN -> GPIO0，左 DIN -> GPIO2，后 DIN -> GPIO1，VCC -> 5V，GND -> GND
 */

#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include "pinctrl.h"
#include "i2c.h"
#include "gpio.h"
#include "hal_gpio.h"
#include "pwm.h"
#include "soc_osal.h"
#include "osal_debug.h"
#include "app_init.h"
#include "ssd1306.h"
#include "ssd1306_fonts.h"
#include "icm20948.h"
#include "ld2450.h"
#include "mqtt_report.h"
#include "ws2812.h"
#include "logo_splash.h"

#define ICM_I2C_BUS_ID          1
#define ICM_I2C_SCL_PIN         16      /* GPIO16 复用为 I2C1_SCL */
#define ICM_I2C_SDA_PIN         15      /* GPIO15 复用为 I2C1_SDA */
#define ICM_I2C_PIN_MODE        2
#define ICM_I2C_BAUDRATE        400000
#define ICM_I2C_HSCODE          0x0

#define ICM_TASK_STACK_SIZE     0x1000
#define ICM_TASK_PRIO           17
#define ICM_SAMPLE_PERIOD_MS    20      /* 50Hz 采样解算 */
#define ICM_PRINT_INTERVAL      25      /* 每 25 次采样打印一次（500ms） */
#define ICM_INIT_RETRY_DELAY_MS 1000

#define COMP_FILTER_ALPHA       0.98f   /* 互补滤波系数：陀螺仪占比 */
#define RAD_TO_DEG              57.29578f

/* 蜂鸣器：交通灯板 BEEP 接底板 A9 = 核心板 GPIO9，PWM 通道 1（参考 beep 样例）。
 * 音高由 PWM 周期决定，音量由占空比粗调（越小越轻）。 */
#define BEEP_PIN                9
#define BEEP_PIN_MODE           1
#define BEEP_PWM_CHANNEL        1
#define BEEP_PWM_GROUP          1
#define BEEP_PWM_CLK_HZ         32000000U /* PWM 计数时钟；若实测音高整体偏移按真实时钟改这里 */

/* 音效设计（与灯效同一节奏源）：摔倒=高低双音啁啾（与双闪同步，最响）；
 * 雷达=短促单音提示（中等音量）；转向=继电器"嗒"声（最轻）；开机=三连升调迎宾音 */
#define SND_FALL_HI_HZ          2700U   /* 摔倒啁啾高音，压在蜂鸣器 ~2.7kHz 共振区上最响 */
#define SND_FALL_LO_HZ          2160U   /* 摔倒啁啾低音，仍在人耳敏感带内 */
#define SND_FALL_SEG_TICKS      8       /* 高/低音各响 160ms（占空比 50% 已是响度上限，靠时长和共振加大音量） */
#define SND_ALARM_DUTY          50U
#define SND_RADAR_HZ            2400U   /* 雷达提示音，靠近蜂鸣器共振区保证响度 */
#define SND_RADAR_BLIP_TICKS    4       /* 单声 80ms，每脉冲周期响两声（双短哔） */
#define SND_RADAR_GAP_TICKS     3       /* 两声间隔 60ms */
#define SND_RADAR_DUTY          40U
#define SND_TURN_HZ             1000U   /* 转向"嗒"声 */
#define SND_TURN_TICKS          2       /* 每流水循环响 40ms */
#define SND_TURN_DUTY           8U
#define SND_CHIME_DUTY          25U     /* 开机迎宾音音量 */

/* 摔倒检测参数（基于 50Hz 采样）。2026-07-07 调灵敏：真实摔倒的失重段往往不够
 * "干净"（人体有支撑、旋转），阈值放宽到 0.6g/40ms；撞击峰值在 50Hz 采样下容易
 * 被采漏，门限降到 2.0g；老人摔倒动作慢，撞击等待窗放宽到 1.5s。若误报变多，
 * 优先把 FALL_FREEFALL_THRESH_G 往回调（0.5g）。 */
#define FALL_FREEFALL_THRESH_G  0.6f    /* 失重判定阈值 */
#define FALL_IMPACT_THRESH_G    2.0f    /* 撞击判定阈值 */
#define FALL_FREEFALL_MIN_CNT   2       /* 连续 2 个采样点（40ms）失重才算 */
#define FALL_IMPACT_WINDOW_CNT  75      /* 失重后 1.5s 内等待撞击 */

/* 消音按键（按下为低电平）：核心板 USER 键接 GPIO13，交通灯扩展板按键接 GPIO14，两个都支持 */
#define BUTTON_GPIO_BOARD       13
#define BUTTON_GPIO_EXPANSION   14

/* WS2812 灯效（右/左/后 = GPIO0/2/1）：开机动画后熄灭待机；
 * 摔倒报警三条红色应急双闪（最高优先级），雷达来车后灯带红色心跳脉冲，
 * 偏头转向对应侧琥珀流水。颜色值为伽马校正前的原始值，由 ws2812_strip_frame 统一校正 */
#define FX_FALL_CYCLE           40      /* 双闪周期 40 x 20ms = 800ms */
#define FX_FALL_FLASH_TICKS     4       /* 单次闪光 80ms */
#define FX_FALL_GAP_TICKS       4       /* 两次闪光间隔 80ms */
#define FX_RADAR_CYCLE          32      /* 心跳脉冲周期 640ms */
#define FX_RADAR_ATTACK_TICKS   4       /* 攻起 80ms */
#define FX_RADAR_DECAY_TICKS    16      /* 衰减 320ms，余下为暗场 */
#define FX_TURN_CYCLE           28      /* 流水转向周期：填充 400ms + 熄灭 160ms */
#define FX_TURN_FILL_TICKS      20
#define FX_ALARM_LEVEL          200     /* 报警红亮度（伽马前），显眼优先 */
#define FX_TURN_R               220     /* 琥珀色（伽马前） */
#define FX_TURN_G               90

/* 偏头转向灯：用加速度计 Z-Y 平面的倾角 atan2(ay, az) 判定左/右偏头，对应侧灯带
 * 黄灯闪烁（转向灯节拍）。戴正时倾角 ~0°，偏头时重力在 YZ 平面的投影随之旋转。
 * 以重力为基准无漂移；ON/OFF 双阈值做迟滞，防止在阈值附近抖动；一级低通抑制
 * 颠簸/撞击带来的瞬时加速度干扰。
 * 若实测方向相反，把两处倾角比较的正负号对调；若模块装反（az 静止为 -1g），
 * 倾角基线在 ±180°，需按佩戴方位调整判定式。 */
/* 阈值按骑行实测考量：看路/颠簸/瞟后视的无意识侧头一般 <25°，刻意打手势 40° 很轻松，
 * 取 40° 触发保证"亮灯=明确变道意图"；回正线放宽到 20°，持灯期间头部小幅回弹不断闪 */
#define HEAD_TILT_ON_DEG        40.0f   /* 触发阈值 */
#define HEAD_TILT_OFF_DEG       20.0f   /* 回正判定阈值（需小于触发阈值） */
#define HEAD_ZY_LPF_ALPHA       0.25f   /* 倾角一级低通系数（约 4 个采样收敛，80ms 滞后） */

typedef enum {
    HEAD_TURN_NONE,
    HEAD_TURN_LEFT,
    HEAD_TURN_RIGHT,
} head_turn_t;

/* 雷达任务与快速逼近判定参数（从 radar 样例移植，判定逻辑不变） */
#define RADAR_TASK_STACK_SIZE   0x1000
#define RADAR_TASK_PRIO         17
#define MQTT_TASK_STACK_SIZE    0x1000
#define MQTT_TASK_PRIO          24      /* 网络任务优先级低于采样/雷达任务，参考 mqtt 样例 */
#define RADAR_IDLE_DELAY_MS     20
#define RADAR_INIT_RETRY_MS     1000
#define RADAR_PRINT_INTERVAL    10      /* 每 10 帧打印一次目标信息 */
#define RADAR_DIAG_INTERVAL_CNT 150     /* 150 x 20ms = 3s 无帧则打印诊断信息 */
#define RADAR_SELFTEST_ROUNDS   20      /* 自检采样轮数 */
#define RADAR_SELFTEST_SAMPLES_PER_ROUND 500 /* 每轮每脚 500 次采样（10 脚同扫） */
#define RADAR_SELFTEST_ROUND_GAP_MS 10  /* 轮间隔，总窗口约 0.5s，覆盖多个雷达帧 */
#define RISK_X_ABS_MAX_MM       1500    /* 目标在正前方 ±1.5m 内 */
#define RISK_Y_MIN_MM           100
#define RISK_Y_MAX_MM           4000    /* 预警距离 0.1~4m */
/* 2026-07-08 降低触发门限：LD2450 上报速度量化很粗（0/8/24cm/s 跳变），30 的速度
 * 门限经常够不着导致报警滞后；帧间差分也放宽，让首个逼近帧就能触发 */
#define RISK_APPROACH_DELTA_MM  15      /* 相邻两帧 y 至少减小 15mm */
#define RISK_APPROACH_SPEED_MIN_CM_S 15 /* 径向速度 ≥15cm/s 即算快速接近 */
#define RADAR_ALARM_CNT         60      /* 报警持续 60 个采样周期（1.2s），与 radar 样例一致 */
#define MM_PER_M                1000

typedef enum {
    FALL_STATE_NORMAL,      /* 正常监测 */
    FALL_STATE_FREEFALL,    /* 检测到失重，等待撞击确认 */
    FALL_STATE_ALARM,       /* 确认摔倒，报警中 */
} fall_state_t;

static volatile bool g_mute_request = false;

/* 雷达任务 -> 主任务共享状态（int16/bool 单字写入，无需加锁） */
static volatile bool g_radar_target_valid = false;
static volatile int16_t g_radar_y_mm = 0;
static volatile int16_t g_radar_speed_cm_s = 0;
static volatile uint32_t g_radar_alarm_cnt = 0; /* >0 表示雷达报警中，主循环每周期递减 */

/* 按键中断：仅置标志，具体消音动作在采样循环里处理 */
static void button_isr(pin_t pin, uintptr_t param)
{
    unused(param);
    g_mute_request = true;
    osal_printk("fall: button %d pressed\r\n", pin);
}

/* 按键引脚初始化：GPIO 输入 + 内部上拉 + 下降沿中断 */
static void button_init(pin_t pin)
{
    uapi_pin_set_mode(pin, HAL_PIO_FUNC_GPIO);
    uapi_pin_set_pull(pin, PIN_PULL_TYPE_UP);
    gpio_select_core(pin, CORES_APPS_CORE);
    uapi_gpio_set_dir(pin, GPIO_DIRECTION_INPUT);
    if (uapi_gpio_register_isr_func(pin, GPIO_INTERRUPT_FALLING_EDGE, button_isr) != ERRCODE_SUCC) {
        uapi_gpio_unregister_isr_func(pin);
        osal_printk("icm20948: button %d isr register failed\r\n", pin);
    }
}

static void beep_init(void)
{
    uapi_pin_set_mode(BEEP_PIN, BEEP_PIN_MODE);
}

/* 发声/停声：freq_hz=0 静音；duty_pct 粗调音量。参考 trafficlight 官方 demo，
 * 每次变调都完整 init/open/start，停声用 close+deinit（本板 PWM 驱动只有
 * deinit 能可靠停掉持续波形）。同一音调重复调用直接返回，不重配。 */
static void beep_tone(uint32_t freq_hz, uint32_t duty_pct)
{
    static uint32_t beep_key = 0;
    uint32_t key = (freq_hz == 0) ? 0 : ((freq_hz << 7) | duty_pct);
    if (key == beep_key) {
        return;
    }
    if (beep_key != 0) { /* 停掉当前波形 */
        uapi_pwm_close(BEEP_PWM_CHANNEL);
        uapi_pwm_deinit();
    }
    beep_key = key;
    if (freq_hz == 0) {
        return;
    }
    uint32_t period = BEEP_PWM_CLK_HZ / freq_hz;
    uint32_t high = period * duty_pct / 100U;
    pwm_config_t cfg = { period - high, high, 0, 1, true };
    uapi_pwm_init();
    uapi_pwm_open(BEEP_PWM_CHANNEL, &cfg);
    uint8_t channel_id = BEEP_PWM_CHANNEL;
    uapi_pwm_set_group(BEEP_PWM_GROUP, &channel_id, 1);
    uapi_pwm_start(BEEP_PWM_GROUP);
}

/* 开机迎宾音：C6-E6-G6 大三和弦琶音升调，在开机动画结束后播放表示"就绪" */
static void beep_boot_chime(void)
{
    static const struct {
        uint16_t hz;
        uint16_t ms;
    } notes[] = { { 1047, 90 }, { 1319, 90 }, { 1568, 160 } };
    for (uint32_t i = 0; i < sizeof(notes) / sizeof(notes[0]); i++) {
        beep_tone(notes[i].hz, SND_CHIME_DUTY);
        osal_msleep(notes[i].ms);
        beep_tone(0, 0);
        osal_msleep(30); /* 音符间隙，让琶音颗粒分明 */
    }
}

/* 将浮点数格式化为两位小数字符串，规避本 SDK printf 不支持 %f 的问题 */
static void float_to_str(float v, char *buf, uint32_t size)
{
    unused(size);
    int32_t scaled = (int32_t)(v * 100.0f); /* 放大 100 倍保留两位小数 */
    const char *sign = (v < 0.0f) ? "-" : "";
    if (scaled < 0) {
        scaled = -scaled;
    }
    if (sprintf(buf, "%s%d.%02d", sign, scaled / 100, scaled % 100) < 0) { /* 整数部分.两位小数 */
        buf[0] = '\0';
    }
}

/* 互补滤波：陀螺仪积分做主，加速度计低频校正，磁力计辅助解算航向角 */
static void attitude_update(const icm20948_data_t *d, float dt, float *pitch, float *roll, float *yaw)
{
    float ax = d->accel_g[0];
    float ay = d->accel_g[1];
    float az = d->accel_g[2];

    float acc_pitch = atan2f(-ax, sqrtf(ay * ay + az * az)) * RAD_TO_DEG;
    float acc_roll = atan2f(ay, az) * RAD_TO_DEG;

    *pitch = COMP_FILTER_ALPHA * (*pitch + d->gyro_dps[1] * dt) + (1.0f - COMP_FILTER_ALPHA) * acc_pitch;
    *roll = COMP_FILTER_ALPHA * (*roll + d->gyro_dps[0] * dt) + (1.0f - COMP_FILTER_ALPHA) * acc_roll;

    /* 磁力计倾斜补偿求航向角，磁场全零（未就绪）时跳过 */
    float mx = d->mag_ut[0];
    float my = d->mag_ut[1];
    float mz = d->mag_ut[2];
    if (mx != 0.0f || my != 0.0f || mz != 0.0f) {
        float pitch_rad = *pitch / RAD_TO_DEG;
        float roll_rad = *roll / RAD_TO_DEG;
        float xh = mx * cosf(pitch_rad) + mz * sinf(pitch_rad);
        float yh = mx * sinf(roll_rad) * sinf(pitch_rad) + my * cosf(roll_rad) - mz * sinf(roll_rad) * cosf(pitch_rad);
        *yaw = atan2f(-yh, xh) * RAD_TO_DEG;
    }
}

/* 摔倒检测状态机：失重(<0.6g 持续 40ms) -> 1.5s 内撞击(>2.0g) -> 报警，
 * 持续到按键确认消音（不自动停止，保证告警不被漏掉）。
 * 每个采样周期调用一次，返回当前状态；声光由 led_fx_update 统一驱动。 */
static fall_state_t fall_detect_update(float acc_norm_g)
{
    static fall_state_t state = FALL_STATE_NORMAL;
    static uint32_t cnt = 0;
    static uint32_t freefall_cnt = 0;

    if (state != FALL_STATE_ALARM) {
        g_mute_request = false; /* 非报警状态的按键按下不生效，丢弃 */
    }

    switch (state) {
        case FALL_STATE_NORMAL:
            if (acc_norm_g < FALL_FREEFALL_THRESH_G) {
                if (++freefall_cnt >= FALL_FREEFALL_MIN_CNT) {
                    state = FALL_STATE_FREEFALL;
                    cnt = 0;
                    osal_printk("fall: freefall detected, waiting impact...\r\n");
                }
            } else {
                freefall_cnt = 0;
            }
            break;
        case FALL_STATE_FREEFALL:
            if (acc_norm_g > FALL_IMPACT_THRESH_G) {
                state = FALL_STATE_ALARM;
                cnt = 0;
                freefall_cnt = 0;
                g_fall_report.fall_count++; /* 触发 MQTT 任务立即上报一次 */
                osal_printk("fall: FALL DETECTED! alarm on\r\n");
            } else if (++cnt > FALL_IMPACT_WINDOW_CNT) {
                state = FALL_STATE_NORMAL; /* 超时没有撞击，虚警排除 */
                freefall_cnt = 0;
            }
            break;
        case FALL_STATE_ALARM:
        default:
            if (g_mute_request) { /* 按键主动消音 */
                g_mute_request = false;
                state = FALL_STATE_NORMAL;
                osal_printk("fall: alarm muted by button\r\n");
            }
            break;
    }
    return state;
}

/* ===== 灯效引擎 =====
 * 每个采样周期（20ms）调用一次 led_fx_update，按优先级为三条灯带渲染逐灯帧：
 *   摔倒（最高）：三条红色应急双闪（80ms x2 连闪 + 暗场，急救车节奏）；
 *   雷达来车：后灯带红色心跳脉冲（快攻起、缓衰减，与双闪明确区分）；
 *   偏头转向：对应侧灯带琥珀流水（从根部逐颗点亮扫出，车规动态转向灯样式）；
 *   待机：熄灭。
 * 帧经 ws2812_strip_frame 发送（内部伽马校正）；与上一帧逐字节比对，内容不变不发送。 */
static uint8_t g_fx_last[WS2812_STRIP_NUM][WS2812_LED_MAX * 3];
static bool g_fx_valid[WS2812_STRIP_NUM] = { false, false, false };

static void fx_send_if_changed(ws2812_strip_t strip, const uint8_t *fb)
{
    uint32_t bytes = (uint32_t)ws2812_strip_len(strip) * 3U;
    if (g_fx_valid[strip] && memcmp(g_fx_last[strip], fb, bytes) == 0) {
        return;
    }
    memcpy(g_fx_last[strip], fb, bytes);
    g_fx_valid[strip] = true;
    ws2812_strip_frame(strip, fb);
}

static void fx_fill(uint8_t *fb, uint32_t leds, uint8_t r, uint8_t g, uint8_t b)
{
    for (uint32_t led = 0; led < leds; led++) {
        fb[led * 3 + 0] = r;
        fb[led * 3 + 1] = g;
        fb[led * 3 + 2] = b;
    }
}

/* 应急双闪：亮-灭-亮-长暗，一眼即知出事了 */
static void fx_render_fall(uint8_t *fb, uint32_t leds, uint32_t tick)
{
    uint32_t t = tick % FX_FALL_CYCLE;
    bool on = (t < FX_FALL_FLASH_TICKS) ||
              (t >= FX_FALL_FLASH_TICKS + FX_FALL_GAP_TICKS &&
               t < FX_FALL_FLASH_TICKS * 2 + FX_FALL_GAP_TICKS);
    fx_fill(fb, leds, on ? FX_ALARM_LEVEL : 0, 0, 0);
}

/* 心跳脉冲：线性攻起 + 线性衰减 + 暗场，节奏催促但不刺眼 */
static void fx_render_radar(uint8_t *fb, uint32_t leds, uint32_t tick)
{
    uint32_t t = tick % FX_RADAR_CYCLE;
    uint32_t level = 0;
    if (t < FX_RADAR_ATTACK_TICKS) {
        level = FX_ALARM_LEVEL * (t + 1) / FX_RADAR_ATTACK_TICKS;
    } else if (t < FX_RADAR_ATTACK_TICKS + FX_RADAR_DECAY_TICKS) {
        level = FX_ALARM_LEVEL * (FX_RADAR_ATTACK_TICKS + FX_RADAR_DECAY_TICKS - t) / FX_RADAR_DECAY_TICKS;
    }
    fx_fill(fb, leds, (uint8_t)level, 0, 0);
}

/* 琥珀流水转向：从灯带根部逐颗填充到末端，随后短暂熄灭再来一轮 */
static void fx_render_turn(uint8_t *fb, uint32_t leds, uint32_t tick)
{
    uint32_t t = tick % FX_TURN_CYCLE;
    uint32_t fill = (t < FX_TURN_FILL_TICKS) ? (leds * (t + 1) / FX_TURN_FILL_TICKS) : 0;
    for (uint32_t led = 0; led < leds; led++) {
        bool on = (led < fill);
        fb[led * 3 + 0] = on ? FX_TURN_R : 0;
        fb[led * 3 + 1] = on ? FX_TURN_G : 0;
        fb[led * 3 + 2] = 0;
    }
}

static void led_fx_update(bool fall_alarm, bool radar_alarm, head_turn_t head_turn)
{
    /* 各灯带独立的动画时基：模式切换时清零，让流水/双闪从头开始 */
    static uint32_t tick[WS2812_STRIP_NUM] = { 0 };
    static uint8_t last_mode[WS2812_STRIP_NUM] = { 0 };
    uint8_t fb[WS2812_LED_MAX * 3];

    for (uint32_t s = 0; s < WS2812_STRIP_NUM; s++) {
        uint8_t mode = 0; /* 0=灭 1=摔倒 2=雷达 3=转向 */
        if (fall_alarm) {
            mode = 1;
        } else if (s == WS2812_STRIP_REAR && radar_alarm) {
            mode = 2;
        } else if ((s == WS2812_STRIP_LEFT && head_turn == HEAD_TURN_LEFT) ||
                   (s == WS2812_STRIP_RIGHT && head_turn == HEAD_TURN_RIGHT)) {
            mode = 3;
        }
        if (mode != last_mode[s]) {
            last_mode[s] = mode;
            tick[s] = 0;
        }
        uint32_t leds = ws2812_strip_len((ws2812_strip_t)s);
        switch (mode) {
            case 1:
                fx_render_fall(fb, leds, tick[s]);
                break;
            case 2:
                fx_render_radar(fb, leds, tick[s]);
                break;
            case 3:
                fx_render_turn(fb, leds, tick[s]);
                break;
            default:
                fx_fill(fb, leds, 0, 0, 0);
                break;
        }
        fx_send_if_changed((ws2812_strip_t)s, fb);
        tick[s]++;
    }

    /* 音效与灯效共用节奏：摔倒=高低双音啁啾（与双闪同步），雷达=短促单音，
     * 转向=每轮流水起始的轻"嗒"声。模式切换时时基清零，与灯光同一拍起步 */
    static uint32_t snd_tick = 0;
    static uint8_t snd_last_mode = 0;
    uint8_t snd_mode = fall_alarm ? 1 : (radar_alarm ? 2 : ((head_turn != HEAD_TURN_NONE) ? 3 : 0));
    if (snd_mode != snd_last_mode) {
        snd_last_mode = snd_mode;
        snd_tick = 0;
    }
    if (snd_mode == 1) {
        /* 高音盖住第一闪+间隙，低音从第二闪延续到暗场开头，各 160ms */
        uint32_t t = snd_tick % FX_FALL_CYCLE;
        if (t < SND_FALL_SEG_TICKS) {
            beep_tone(SND_FALL_HI_HZ, SND_ALARM_DUTY);
        } else if (t < SND_FALL_SEG_TICKS * 2) {
            beep_tone(SND_FALL_LO_HZ, SND_ALARM_DUTY);
        } else {
            beep_tone(0, 0);
        }
    } else if (snd_mode == 2) {
        /* 双短哔与灯光脉冲同拍起步："哔哔……哔哔……"，比单声更有催促感 */
        uint32_t t = snd_tick % FX_RADAR_CYCLE;
        bool on = (t < SND_RADAR_BLIP_TICKS) ||
                  (t >= SND_RADAR_BLIP_TICKS + SND_RADAR_GAP_TICKS &&
                   t < SND_RADAR_BLIP_TICKS * 2 + SND_RADAR_GAP_TICKS);
        beep_tone(on ? SND_RADAR_HZ : 0, SND_RADAR_DUTY);
    } else if (snd_mode == 3) {
        uint32_t t = snd_tick % FX_TURN_CYCLE;
        beep_tone((t < SND_TURN_TICKS) ? SND_TURN_HZ : 0, SND_TURN_DUTY);
    } else {
        beep_tone(0, 0);
    }
    snd_tick++;
}

static int16_t abs_i16(int16_t value)
{
    return (value < 0) ? (int16_t)(0 - value) : value;
}

/* 快速逼近判定（radar 样例原逻辑）：目标在正前方预警区内、y 距离较上一帧
 * 明显减小且径向速度足够大，三个条件同时满足才算危险 */
static bool target_is_fast_approaching(uint8_t index, const ld2450_target_t *target)
{
    static bool prev_valid[LD2450_TARGET_NUM] = { false };
    static int16_t prev_y_mm[LD2450_TARGET_NUM] = { 0 };

    if (target == NULL || !target->valid) {
        prev_valid[index] = false;
        return false;
    }

    bool in_danger_area = (abs_i16(target->x_mm) <= RISK_X_ABS_MAX_MM &&
                           target->y_mm >= RISK_Y_MIN_MM &&
                           target->y_mm <= RISK_Y_MAX_MM);
    int16_t approach_delta_mm = 0;
    if (prev_valid[index]) {
        approach_delta_mm = (int16_t)(prev_y_mm[index] - target->y_mm);
    }
    bool y_is_approaching_fast = (prev_valid[index] && approach_delta_mm >= RISK_APPROACH_DELTA_MM);
    bool speed_is_large = (abs_i16(target->speed_cm_s) >= RISK_APPROACH_SPEED_MIN_CM_S);

    prev_valid[index] = true;
    prev_y_mm[index] = target->y_mm;
    return (in_danger_area && y_is_approaching_fast && speed_is_large);
}

/* 处理一帧雷达数据：更新共享的最近目标信息，检测到快速逼近则拉起报警 */
static void radar_handle_frame(const ld2450_frame_t *frame)
{
    static uint32_t frame_cnt = 0;
    bool emergency = false;
    const ld2450_target_t *nearest = NULL;

    for (uint8_t i = 0; i < LD2450_TARGET_NUM; i++) {
        const ld2450_target_t *target = &frame->target[i];
        if (target_is_fast_approaching(i, target)) {
            emergency = true;
        }
        if (!target->valid) {
            continue;
        }
        if (nearest == NULL || abs_i16(target->y_mm) < abs_i16(nearest->y_mm)) {
            nearest = target;
        }
    }

    g_radar_target_valid = (nearest != NULL);
    if (nearest != NULL) {
        g_radar_y_mm = nearest->y_mm;
        g_radar_speed_cm_s = nearest->speed_cm_s;
    }

    if (emergency) {
        if (g_radar_alarm_cnt == 0) {
            osal_printk("radar: Emergency! fast approaching\r\n");
        }
        g_radar_alarm_cnt = RADAR_ALARM_CNT;
    }

    if (++frame_cnt >= RADAR_PRINT_INTERVAL) {
        frame_cnt = 0;
        if (nearest != NULL) {
            osal_printk("radar: x=%dmm y=%dmm speed=%dcm/s\r\n",
                        nearest->x_mm, nearest->y_mm, nearest->speed_cm_s);
        } else {
            osal_printk("radar: no target\r\n");
        }
    }
}

/* 雷达 RX 找线自检：把排针上所有空闲 GPIO 配成下拉输入同时采样。
 * 串口 TX 空闲为高、发数据时翻转，所以雷达 TX 实际插在哪个脚，哪个脚就会
 * 读到高电平+翻转——不用信底板丝印。扫完把 GPIO7 交还 UART2。
 * 扫描范围排除在用脚：0/1/2(三条灯带)/9(蜂鸣器)/13/14(按键)/15/16(I2C1)，4/5(SSI 调试口)不动。 */
static void radar_rx_line_selftest(void)
{
    static const uint8_t scan_pins[] = { 3, 6, 7, 8, 10, 11, 12 };
    const uint32_t pin_num = sizeof(scan_pins) / sizeof(scan_pins[0]);
    uint32_t high_cnt[sizeof(scan_pins)] = { 0 };
    uint32_t toggle_cnt[sizeof(scan_pins)] = { 0 };
    gpio_level_t last[sizeof(scan_pins)];

    for (uint32_t p = 0; p < pin_num; p++) {
        uapi_pin_set_mode(scan_pins[p], HAL_PIO_FUNC_GPIO);
        gpio_select_core(scan_pins[p], CORES_APPS_CORE);
        uapi_pin_set_pull(scan_pins[p], PIN_PULL_TYPE_DOWN);
        uapi_gpio_set_dir(scan_pins[p], GPIO_DIRECTION_INPUT);
    }
    osal_msleep(10); /* 等下拉稳定 */
    for (uint32_t p = 0; p < pin_num; p++) {
        last[p] = uapi_gpio_get_val(scan_pins[p]);
    }

    uint32_t total = 0;
    for (uint32_t round = 0; round < RADAR_SELFTEST_ROUNDS; round++) {
        for (uint32_t i = 0; i < RADAR_SELFTEST_SAMPLES_PER_ROUND; i++) {
            total++;
            for (uint32_t p = 0; p < pin_num; p++) {
                gpio_level_t cur = uapi_gpio_get_val(scan_pins[p]);
                if (cur == GPIO_LEVEL_HIGH) {
                    high_cnt[p]++;
                }
                if (cur != last[p]) {
                    toggle_cnt[p]++;
                    last[p] = cur;
                }
            }
        }
        osal_msleep(RADAR_SELFTEST_ROUND_GAP_MS); /* 分散采样窗口，覆盖多个雷达帧周期 */
    }

    bool found = false;
    for (uint32_t p = 0; p < pin_num; p++) {
        if (high_cnt[p] == 0) {
            continue;
        }
        found = true;
        osal_printk("radar: wire-scan LIVE pin gpio%d high=%d/%d toggles=%d%s\r\n",
                    scan_pins[p], high_cnt[p], total, toggle_cnt[p],
                    (scan_pins[p] == LD2450_UART_RXD_PIN) ? " (correct: this is UART2 RX)" :
                    " <-- radar TX is here, move it to GPIO7");
    }
    if (!found) {
        osal_printk("radar: wire-scan no live pin, radar unpowered or wire broken "
                    "(also check button gpio13/14 prints)\r\n");
    }
    uapi_pin_set_pull(LD2450_UART_RXD_PIN, PIN_PULL_TYPE_DISABLE); /* 撤掉下拉，交还 UART2 */
}

/* 雷达任务：UART2 收流 -> 逐字节解析 -> 每帧更新共享状态 */
static void radar_task(void)
{
    uint8_t rx_buffer[LD2450_UART_RX_BUFFER_SIZE] = { 0 };
    ld2450_parser_t parser;
    ld2450_frame_t frame;

    Ld2450ParserInit(&parser);
    radar_rx_line_selftest();
    while (Ld2450UartInit() != ERRCODE_SUCC) {
        osal_printk("radar: uart%d init failed, retry...\r\n", LD2450_UART_BUS_ID);
        osal_msleep(RADAR_INIT_RETRY_MS);
    }
    osal_printk("radar: ld2450 uart%d started (rx=gpio%d tx=gpio%d)\r\n",
                LD2450_UART_BUS_ID, LD2450_UART_RXD_PIN, LD2450_UART_TXD_PIN);

    uint32_t loop_cnt = 0;
    uint32_t rx_bytes = 0;
    uint32_t rx_frames = 0;
    while (1) {
        int32_t len = Ld2450UartRead(rx_buffer, sizeof(rx_buffer));
        for (int32_t i = 0; i < len; i++) {
            if (Ld2450ParseByte(&parser, rx_buffer[i], &frame)) {
                rx_frames++;
                radar_handle_frame(&frame);
            }
        }
        if (len > 0) {
            rx_bytes += (uint32_t)len;
        }
        /* 诊断：每 3s 无有效帧时报告原始字节数，区分接线断(0 字节)与波特率错(有字节无帧) */
        if (++loop_cnt >= RADAR_DIAG_INTERVAL_CNT) {
            loop_cnt = 0;
            if (rx_frames == 0) {
                osal_printk("radar: no frame in 3s, raw rx bytes=%d %s\r\n", rx_bytes,
                            (rx_bytes == 0) ? "(check wiring: TX->GPIO7, 5V, GND)" : "(check baudrate 256000)");
            }
            rx_bytes = 0;
            rx_frames = 0;
        }
        osal_msleep(RADAR_IDLE_DELAY_MS);
    }
}

static void icm20948_task(void)
{
    /* I2C1 引脚复用与总线初始化 */
    uapi_pin_set_mode(ICM_I2C_SCL_PIN, ICM_I2C_PIN_MODE);
    uapi_pin_set_mode(ICM_I2C_SDA_PIN, ICM_I2C_PIN_MODE);
    errcode_t ret = uapi_i2c_master_init(ICM_I2C_BUS_ID, ICM_I2C_BAUDRATE, ICM_I2C_HSCODE);
    if (ret != ERRCODE_SUCC) {
        osal_printk("icm20948: i2c init failed, ret = 0x%x\r\n", ret);
    }

    /* 蜂鸣器 PWM 初始化（GPIO9，先不发声） */
    beep_init();

    /* OLED 与 ICM20948 共用 I2C1 总线（地址 0x3C / 0x68 不冲突）。
     * 先亮竞赛 logo 开机画面，随后 3.2s 的灯带开机动画期间持续展示 */
    ssd1306_Init();
    ssd1306_DrawBitmap(LOGO_SPLASH_128X64, sizeof(LOGO_SPLASH_128X64));
    ssd1306_UpdateScreen();

    /* WS2812 三条灯带：时序校准 + 接线自检 + 开机动画（约 3.2s） */
    ws2812_init();

    /* 开机动画结束后播放迎宾音，表示系统就绪 */
    beep_boot_chime();

    /* 消音按键：核心板 USER 键(GPIO13) + 扩展板按键(GPIO14)，任一均可消音 */
    button_init(BUTTON_GPIO_BOARD);
    button_init(BUTTON_GPIO_EXPANSION);

    while (icm20948_init(ICM_I2C_BUS_ID) != 0) {
        osal_printk("icm20948: init failed, retry...\r\n");
        osal_msleep(ICM_INIT_RETRY_DELAY_MS);
    }
    osal_printk("icm20948: init success!\r\n");

    icm20948_data_t data = { 0 };
    float pitch = 0.0f;
    float roll = 0.0f;
    float yaw = 0.0f;
    uint32_t count = 0;
    uint32_t fail_count = 0;
    const float dt = ICM_SAMPLE_PERIOD_MS / 1000.0f;
    head_turn_t head_turn = HEAD_TURN_NONE; /* 偏头状态（带迟滞） */
    float head_zy_deg = 0.0f;              /* ZY 平面倾角（低通后） */

    while (1) {
        osal_msleep(ICM_SAMPLE_PERIOD_MS);
        if (icm20948_read_data(&data) != 0) {
            if (++fail_count >= 10) { /* 连续失败 10 次则重新初始化 */
                fail_count = 0;
                osal_printk("icm20948: too many failures, reinit...\r\n");
                while (icm20948_init(ICM_I2C_BUS_ID) != 0) {
                    osal_msleep(ICM_INIT_RETRY_DELAY_MS);
                }
            }
            continue;
        }
        fail_count = 0;
        attitude_update(&data, dt, &pitch, &roll, &yaw);

        /* 雷达报警消音要在摔倒状态机之前处理，否则按键标志会被状态机清掉 */
        if (g_mute_request && g_radar_alarm_cnt > 0) {
            g_radar_alarm_cnt = 0;
            osal_printk("radar: alarm muted by button\r\n");
        }

        /* 摔倒检测：每个采样周期都要跑，保证不漏掉瞬时的失重/撞击 */
        float acc_norm = sqrtf(data.accel_g[0] * data.accel_g[0] + data.accel_g[1] * data.accel_g[1] +
                               data.accel_g[2] * data.accel_g[2]);
        fall_state_t fall_state = fall_detect_update(acc_norm);

        /* 云端上报数据快照（MQTT 任务异步读取；极值窗口由 MQTT 任务在上报后复位） */
        g_fall_report.ax_g = data.accel_g[0];
        g_fall_report.ay_g = data.accel_g[1];
        g_fall_report.az_g = data.accel_g[2];
        g_fall_report.acc_norm_g = acc_norm;
        if (acc_norm < g_fall_report.acc_min_g) {
            g_fall_report.acc_min_g = acc_norm;
        }
        if (acc_norm > g_fall_report.acc_max_g) {
            g_fall_report.acc_max_g = acc_norm;
        }
        g_fall_report.fall_state = (uint32_t)fall_state;

        /* 雷达快速逼近报警计时：声光由 led_fx_update 按优先级统一驱动 */
        bool radar_alarm = (g_radar_alarm_cnt > 0);
        if (fall_state != FALL_STATE_ALARM && radar_alarm) {
            --g_radar_alarm_cnt;
        }

        /* 偏头检测（转向灯，带迟滞）：ZY 平面倾角 atan2(ay, az)，负方向超阈值 = 左偏，
         * 正方向 = 右偏，回到 OFF 阈值以内判定回正。方向与佩戴方位有关，实测反了就对调正负号 */
        float zy_deg = atan2f(data.accel_g[1], data.accel_g[2]) * RAD_TO_DEG;
        head_zy_deg += HEAD_ZY_LPF_ALPHA * (zy_deg - head_zy_deg);
        if (head_turn == HEAD_TURN_NONE) {
            /* 佩戴方位实测：zy 正方向 = 左偏（2026-07-08 校准） */
            if (head_zy_deg > HEAD_TILT_ON_DEG) {
                head_turn = HEAD_TURN_LEFT;
                osal_printk("head: turn LEFT (zy=%d)\r\n", (int)head_zy_deg);
            } else if (head_zy_deg < -HEAD_TILT_ON_DEG) {
                head_turn = HEAD_TURN_RIGHT;
                osal_printk("head: turn RIGHT (zy=%d)\r\n", (int)head_zy_deg);
            }
        } else if (fabsf(head_zy_deg) < HEAD_TILT_OFF_DEG) {
            head_turn = HEAD_TURN_NONE;
            osal_printk("head: back to center\r\n");
        }

        /* 灯效渲染：摔倒双闪 > 雷达心跳脉冲(后) > 转向流水(左/右) > 熄灭 */
        led_fx_update(fall_state == FALL_STATE_ALARM, radar_alarm, head_turn);

        if (++count >= ICM_PRINT_INTERVAL) {
            count = 0;
            char s[13][16]; /* 9 轴 + 温度 + 3 个姿态角，共 13 个数值 */
            const float vals[13] = {
                data.accel_g[0], data.accel_g[1], data.accel_g[2],
                data.gyro_dps[0], data.gyro_dps[1], data.gyro_dps[2],
                data.mag_ut[0], data.mag_ut[1], data.mag_ut[2],
                data.temp_c, pitch, roll, yaw,
            };
            for (uint32_t i = 0; i < sizeof(vals) / sizeof(vals[0]); i++) {
                float_to_str(vals[i], s[i], sizeof(s[i]));
            }
            osal_printk("accel[g]: %s %s %s | gyro[dps]: %s %s %s\r\n", s[0], s[1], s[2], s[3], s[4], s[5]);
            osal_printk("mag[uT]: %s %s %s | temp[C]: %s\r\n", s[6], s[7], s[8], s[9]);
            osal_printk("attitude: pitch=%s roll=%s yaw=%s\r\n\r\n", s[10], s[11], s[12]);

            /* OLED 显示（只保留关键信息，四区布局）：
             *   y0~17  状态大字（摔倒 > 雷达 > 转向 > 正常）
             *   y20    分隔线
             *   y26    |a| 加速度模值 + 累计摔倒次数
             *   y40    雷达最近目标距离/速度
             *   y54    底部提示行（报警时提示按键消音）
             * 摔倒报警时整屏反色（白底黑字），远处一眼可见 */
            char line[32] = { 0 };
            char acc_str[16] = { 0 };
            float_to_str(acc_norm, acc_str, sizeof(acc_str));
            SSD1306_COLOR fg = White;
            if (fall_state == FALL_STATE_ALARM) {
                ssd1306_Fill(White);
                fg = Black;
            } else {
                ssd1306_Fill(Black);
            }
            char *st = "NORMAL";
            if (fall_state == FALL_STATE_ALARM) {
                st = "! FALL !";
            } else if (fall_state == FALL_STATE_FREEFALL) {
                st = "FREEFALL";
            } else if (radar_alarm) {
                st = "CAR REAR";
            } else if (head_turn == HEAD_TURN_LEFT) {
                st = "<< LEFT";
            } else if (head_turn == HEAD_TURN_RIGHT) {
                st = "RIGHT >>";
            }
            /* 状态大字居中 */
            uint8_t st_w = (uint8_t)(strlen(st) * 11U);
            ssd1306_SetCursor((uint8_t)((128 - st_w) / 2), 0);
            ssd1306_DrawString(st, Font_11x18, fg);
            ssd1306_DrawRectangle(0, 20, 127, 20, fg); /* 分隔线 */
            ssd1306_SetCursor(0, 26);
            if (sprintf(line, "|a| %sg  falls %d", acc_str, (int)g_fall_report.fall_count) > 0) {
                ssd1306_DrawString(line, Font_7x10, fg);
            }
            ssd1306_SetCursor(0, 40);
            if (g_radar_target_valid) {
                char dist_str[16] = { 0 };
                float_to_str(g_radar_y_mm / (float)MM_PER_M, dist_str, sizeof(dist_str));
                if (sprintf(line, "car %sm  %dcm/s", dist_str, g_radar_speed_cm_s) > 0) {
                    ssd1306_DrawString(line, Font_7x10, fg);
                }
            } else {
                ssd1306_DrawString("car ---", Font_7x10, fg);
            }
            ssd1306_SetCursor(0, 54);
            if (fall_state == FALL_STATE_ALARM || radar_alarm) {
                ssd1306_DrawString("press key to mute", Font_7x10, fg);
            } else {
                ssd1306_DrawString("helmet ready", Font_6x8, fg);
            }
            ssd1306_UpdateScreen();
        }
    }
}

static void icm20948_entry(void)
{
    osal_task *task_handle = NULL;
    osal_kthread_lock();
    task_handle = osal_kthread_create((osal_kthread_handler)icm20948_task, 0, "Icm20948Task", ICM_TASK_STACK_SIZE);
    if (task_handle != NULL) {
        osal_kthread_set_priority(task_handle, ICM_TASK_PRIO);
        osal_kfree(task_handle);
    }
    task_handle = osal_kthread_create((osal_kthread_handler)radar_task, 0, "RadarTask", RADAR_TASK_STACK_SIZE);
    if (task_handle != NULL) {
        osal_kthread_set_priority(task_handle, RADAR_TASK_PRIO);
        osal_kfree(task_handle);
    }
    task_handle = osal_kthread_create((osal_kthread_handler)mqtt_report_task, 0, "MqttReportTask",
                                      MQTT_TASK_STACK_SIZE);
    if (task_handle != NULL) {
        osal_kthread_set_priority(task_handle, MQTT_TASK_PRIO);
        osal_kfree(task_handle);
    }
    osal_kthread_unlock();
}

app_run(icm20948_entry);
