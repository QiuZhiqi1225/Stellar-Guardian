/*
 * Copyright (c) HiSilicon (Shanghai) Technologies Co., Ltd. 2023-2023. All rights reserved.
 * Licensed under the Apache License, Version 2.0.
 *
 * WS2812E 单线协议驱动：0 码 = 高 ~300ns + 低 ~900ns，1 码 = 高 ~900ns + 低 ~300ns，
 * 帧尾拉低 >280us 锁存。uapi_gpio_set_val 一次调用要跑完整个 HAL 栈，做不出亚微秒脉冲，
 * 所以电平翻转直写 GPIO 的 data_set/data_clr 寄存器（写 1 生效，官方驱动输出走的同一组
 * 寄存器），脉宽用上电时以 24MHz TCXO 校准过的空循环控制，与 CPU 主频解耦。
 * 三条灯带的数据脚都在 channel0：同色数据用组合位掩码一次写入同时驱动三条；
 * 逐灯异色（开机彩虹动画）按条依次发帧。
 */
#include <stdint.h>
#include <stdbool.h>
#include <math.h>
#include "pinctrl.h"
#include "gpio.h"
#include "hal_gpio.h"
#include "tcxo.h"
#include "soc_osal.h"
#include "osal_debug.h"
#include "ws2812.h"

#define WS2812_GPIO_CHANNEL   0      /* GPIO0~7 -> channel0/group0，组内位号=引脚号（gpio_porting.c） */
#define WS2812_DATA_SET_OFF   0x30U  /* gpio_data_set 寄存器偏移（hal_gpio_v150_regs_def.h） */
#define WS2812_DATA_CLR_OFF   0x34U  /* gpio_data_clr 寄存器偏移 */
#define WS2812_SW_OUT_OFF     0x00U  /* gpio_sw_out：输入方向下读到的是引脚真实电平（雷达找线自检同款用法） */
#define WS2812_SW_OEN_OFF     0x04U  /* gpio_sw_oen：方向寄存器，位 0=输出/1=输入（HAL_GPIO_DIRECTION_*） */

#define WS2812_T0H_NS         300U
#define WS2812_T0L_NS         900U
#define WS2812_T1H_NS         900U
#define WS2812_T1L_NS         300U
#define WS2812_NS_PER_US      1000U
#define WS2812_CAL_LOOPS      100000U /* 校准空循环次数，实测耗时毫秒级，误差 <1% */
#define WS2812_GRB_BYTES      3
#define WS2812_GAMMA          2.2f    /* 感知亮度伽马，逐灯帧接口统一应用 */

/* 连接自检参数 */
#define WS2812_CHK_SAMPLES      100   /* 上/下拉稳态采样次数 */
#define WS2812_CHK_SETTLE_MS    2     /* 切换上下拉后的稳定等待 */
#define WS2812_CHK_ROUNDS       8     /* 电容法测量轮数 */
#define WS2812_CHK_DISCHARGE_US 5     /* 每轮输出低放电时长 */
#define WS2812_CHK_POLL_LIMIT   2000  /* 充电轮询上限（防呆） */
#define WS2812_FLOAT_POLL_MAX   4     /* 平均圈数不超过它视为裸引脚（悬空未接线） */

/* 开机动画参数：彩虹流光扫过（升余弦亮度窗滑过灯带）+ 冰蓝呼吸一次，共约 3.2s */
#define WS2812_BOOT_LEVEL       160   /* 动画峰值亮度（伽马校正前），兼顾观感与 5V 口负载 */
#define WS2812_BOOT_FRAMES      40    /* 每阶段帧数 */
#define WS2812_BOOT_FRAME_MS    40    /* 帧间隔（25fps），每阶段 1.6s */
#define WS2812_BOOT_WAVE_WIDTH  0.45f /* 流光亮度窗宽度（相对灯带长度） */
#define WS2812_HUE_SPAN         256U  /* 色相环总刻度 */
#define WS2812_PI               3.14159265f

extern uintptr_t g_gpios_regs[];      /* 各 GPIO channel 寄存器基址（hal_gpio_v150_regs_op.c） */

/* 三条灯带的数据脚、灯珠数与名称，下标即 ws2812_strip_t。引脚只能选 GPIO0~7（channel0）。
 * 改脚时记得同步 icm20948_demo.c 雷达找线自检的 scan_pins 排除列表。 */
static const uint8_t g_strip_pins[WS2812_STRIP_NUM] = { 0, 2, 1 };
static const uint8_t g_strip_leds[WS2812_STRIP_NUM] = { 20, 19, 11 };
static const char *const g_strip_names[WS2812_STRIP_NUM] = { "right", "left", "rear" };

static volatile uint32_t *g_ws2812_set_reg = NULL;
static volatile uint32_t *g_ws2812_clr_reg = NULL;
static volatile uint32_t *g_ws2812_in_reg = NULL;
static volatile uint32_t *g_ws2812_oen_reg = NULL;
static uint32_t g_ws2812_strip_bit[WS2812_STRIP_NUM];
static uint32_t g_ws2812_all_mask; /* 三条灯带位掩码的按位或 */
static uint32_t g_ws2812_loops_per_us = 1;
static uint32_t g_ws2812_t0h_loops;
static uint32_t g_ws2812_t0l_loops;
static uint32_t g_ws2812_t1h_loops;
static uint32_t g_ws2812_t1l_loops;
static uint8_t g_ws2812_gamma[256]; /* 伽马 2.2 查找表，init 时生成 */

/* 延时单元：volatile 计数强制每圈访存，保证校准值与实际使用时每圈耗时一致 */
static void ws2812_delay_loops(uint32_t loops)
{
    for (volatile uint32_t i = 0; i < loops; i++) {
    }
}

static uint32_t ws2812_ns_to_loops(uint32_t ns, uint32_t loops_per_us)
{
    uint32_t loops = ns * loops_per_us / WS2812_NS_PER_US;
    return (loops == 0) ? 1 : loops;
}

static void ws2812_timing_calibrate(void)
{
    uint32_t irq = osal_irq_lock();
    uint64_t start_us = uapi_tcxo_get_us();
    ws2812_delay_loops(WS2812_CAL_LOOPS);
    uint64_t elapsed_us = uapi_tcxo_get_us() - start_us;
    osal_irq_restore(irq);

    uint32_t loops_per_us = (elapsed_us != 0) ? (uint32_t)(WS2812_CAL_LOOPS / elapsed_us) : 1;
    g_ws2812_loops_per_us = loops_per_us;
    g_ws2812_t0h_loops = ws2812_ns_to_loops(WS2812_T0H_NS, loops_per_us);
    g_ws2812_t0l_loops = ws2812_ns_to_loops(WS2812_T0L_NS, loops_per_us);
    g_ws2812_t1h_loops = ws2812_ns_to_loops(WS2812_T1H_NS, loops_per_us);
    g_ws2812_t1l_loops = ws2812_ns_to_loops(WS2812_T1L_NS, loops_per_us);
    osal_printk("ws2812: calibrate %u loops/us, t0h/t1h=%u/%u loops\r\n",
                loops_per_us, g_ws2812_t0h_loops, g_ws2812_t1h_loops);
}

/* 阶段1+2：上/下拉稳态检查；阶段3：电容法测充电时间。返回 false 表示已确诊接线故障 */
static bool ws2812_link_check_stages(const char *name, uint8_t pin, uint32_t pin_bit)
{
    /* 阶段1：内部下拉，采样应全低；读到高说明线被外部驱动（接错到别的输出脚） */
    uapi_pin_set_pull(pin, PIN_PULL_TYPE_DOWN);
    uapi_gpio_set_dir(pin, GPIO_DIRECTION_INPUT);
    osal_msleep(WS2812_CHK_SETTLE_MS);
    uint32_t high_cnt = 0;
    for (uint32_t i = 0; i < WS2812_CHK_SAMPLES; i++) {
        if ((*g_ws2812_in_reg & pin_bit) != 0) {
            high_cnt++;
        }
    }
    if (high_cnt != 0) {
        osal_printk("ws2812: %s link check FAIL, din gpio%d driven high externally (%u/%u), wrong wiring?\r\n",
                    name, pin, high_cnt, WS2812_CHK_SAMPLES);
        return false; /* 外部在驱动这条线，跳过阶段3 的灌低测试 */
    }

    /* 阶段2：内部上拉，采样应全高；拉不高说明线被强拉低 */
    uapi_pin_set_pull(pin, PIN_PULL_TYPE_UP);
    osal_msleep(WS2812_CHK_SETTLE_MS);
    uint32_t low_cnt = 0;
    for (uint32_t i = 0; i < WS2812_CHK_SAMPLES; i++) {
        if ((*g_ws2812_in_reg & pin_bit) == 0) {
            low_cnt++;
        }
    }
    if (low_cnt != 0) {
        osal_printk("ws2812: %s link check FAIL, din gpio%d stuck low (%u/%u), check GND short or strip 5V power\r\n",
                    name, pin, low_cnt, WS2812_CHK_SAMPLES);
        return false;
    }

    /* 阶段3：电容法。保持上拉，每轮先输出低把线放电，再松开方向、数充电到高的轮询圈数。
     * 方向切换直写 oen 寄存器（位 0=输出/1=输入）并全程关中断，保证各轮计数条件一致 */
    uint32_t total_polls = 0;
    uint32_t max_polls = 0;
    for (uint32_t round = 0; round < WS2812_CHK_ROUNDS; round++) {
        uint32_t irq = osal_irq_lock();
        *g_ws2812_clr_reg = pin_bit;   /* 输出锁存置低 */
        *g_ws2812_oen_reg &= ~pin_bit; /* 输出使能，放电 */
        ws2812_delay_loops(g_ws2812_loops_per_us * WS2812_CHK_DISCHARGE_US);
        *g_ws2812_oen_reg |= pin_bit;  /* 松开为输入，由内部上拉充电 */
        uint32_t polls = 0;
        while ((*g_ws2812_in_reg & pin_bit) == 0 && polls < WS2812_CHK_POLL_LIMIT) {
            polls++;
        }
        osal_irq_restore(irq);
        total_polls += polls;
        if (polls > max_polls) {
            max_polls = polls;
        }
    }
    uint32_t avg_polls = total_polls / WS2812_CHK_ROUNDS;
    osal_printk("ws2812: %s link rise avg=%u max=%u polls (floating<=%u)\r\n",
                name, avg_polls, max_polls, WS2812_FLOAT_POLL_MAX);
    if (avg_polls <= WS2812_FLOAT_POLL_MAX) {
        osal_printk("ws2812: %s link check WARN, din gpio%d looks unconnected (no load on data line)\r\n",
                    name, pin);
    } else {
        osal_printk("ws2812: %s link check OK, din gpio%d wiring detected\r\n", name, pin);
    }
    return true;
}

/* 数据脚连接自检（逐条灯带，串口输出结论，可随时调用）。WS2812 是单向协议、DIN 高阻输入，
 * 没有任何应答可查，只能靠电气特征判断：外部驱动/对地短路（或灯条未上电，DIN 保护
 * 二极管会把线钳低）能确诊；"已接线/没接线"用线路电容充电时间做启发式区分，
 * 原始圈数一并打印，误报时可对照 floating 阈值调 WS2812_FLOAT_POLL_MAX。
 * 注意：灯珠坏死无法从数据线上检测出来，靠开机彩虹动画肉眼确认。 */
void ws2812_link_check(void)
{
    if (g_ws2812_set_reg == NULL) {
        return;
    }
    for (uint32_t s = 0; s < WS2812_STRIP_NUM; s++) {
        (void)ws2812_link_check_stages(g_strip_names[s], g_strip_pins[s], g_ws2812_strip_bit[s]);
        /* 恢复灯条数据脚的工作状态：禁用上下拉、输出低 */
        uapi_pin_set_pull(g_strip_pins[s], PIN_PULL_TYPE_DISABLE);
        uapi_gpio_set_dir(g_strip_pins[s], GPIO_DIRECTION_OUTPUT);
        uapi_gpio_set_val(g_strip_pins[s], GPIO_LEVEL_LOW);
    }
}

/* 焊盘驱动自检：走发帧同款的 set/clr 寄存器路径驱高、驱低，随后切输入方向，
 * 靠线上电容短暂保持的电平读回验证。连接自检只验证过读取和驱低，这里补上驱高。 */
static void ws2812_pad_drive_test(const char *name, uint32_t pin_bit)
{
    uint32_t irq = osal_irq_lock();
    *g_ws2812_oen_reg &= ~pin_bit; /* 输出方向 */
    *g_ws2812_set_reg = pin_bit;   /* 驱高 */
    ws2812_delay_loops(g_ws2812_loops_per_us * 10U);
    *g_ws2812_oen_reg |= pin_bit;  /* 切输入，线上电容保持电平 */
    ws2812_delay_loops(g_ws2812_loops_per_us);
    uint32_t high_ok = *g_ws2812_in_reg & pin_bit;
    *g_ws2812_oen_reg &= ~pin_bit;
    *g_ws2812_clr_reg = pin_bit;   /* 驱低 */
    ws2812_delay_loops(g_ws2812_loops_per_us * 10U);
    *g_ws2812_oen_reg |= pin_bit;
    ws2812_delay_loops(g_ws2812_loops_per_us);
    uint32_t low_ok = (*g_ws2812_in_reg & pin_bit) == 0;
    *g_ws2812_oen_reg &= ~pin_bit; /* 恢复输出方向，维持低电平 */
    osal_irq_restore(irq);
    osal_printk("ws2812: %s pad drive test: high %s, low %s\r\n",
                name, high_ok ? "OK" : "FAIL", low_ok ? "OK" : "FAIL");
}

/* 按位掩码发送一段 GRB 字节流：mask 内的引脚同时收到同样的数据。
 * 一帧必须连续发完（字节间停顿超过 ~50us 会被灯珠当成帧尾锁存），全程关中断。 */
static void ws2812_send_bytes(uint32_t mask, const uint8_t *data, uint32_t len)
{
    if (g_ws2812_set_reg == NULL || mask == 0) {
        return;
    }
    uint32_t irq = osal_irq_lock();
    *g_ws2812_oen_reg &= ~mask; /* 保险：确保方向为输出（位 0=输出） */
    for (uint32_t i = 0; i < len; i++) {
        uint8_t byte = data[i];
        for (uint8_t bit = 0x80; bit != 0; bit >>= 1) {
            if ((byte & bit) != 0) {
                *g_ws2812_set_reg = mask;
                ws2812_delay_loops(g_ws2812_t1h_loops);
                *g_ws2812_clr_reg = mask;
                ws2812_delay_loops(g_ws2812_t1l_loops);
            } else {
                *g_ws2812_set_reg = mask;
                ws2812_delay_loops(g_ws2812_t0h_loops);
                *g_ws2812_clr_reg = mask;
                ws2812_delay_loops(g_ws2812_t0l_loops);
            }
        }
    }
    osal_irq_restore(irq);
    /* 帧尾保持低电平即自动锁存；调用间隔远大于 280us，无需额外等待 */
}

/* 整条同色的帧缓冲填充。WS2812 发送顺序为 GRB，高位在前 */
static void ws2812_fill_solid(uint8_t *buf, uint32_t leds, uint8_t r, uint8_t g, uint8_t b)
{
    for (uint32_t led = 0; led < leds; led++) {
        buf[led * WS2812_GRB_BYTES + 0] = g;
        buf[led * WS2812_GRB_BYTES + 1] = r;
        buf[led * WS2812_GRB_BYTES + 2] = b;
    }
}

void ws2812_set_all(uint8_t r, uint8_t g, uint8_t b)
{
    uint8_t buf[WS2812_LED_MAX * WS2812_GRB_BYTES];
    /* 按最长灯带的长度发送：短灯带收满自己的颗数后，多余数据从 DOUT 流出无副作用 */
    ws2812_fill_solid(buf, WS2812_LED_MAX, r, g, b);
    ws2812_send_bytes(g_ws2812_all_mask, buf, sizeof(buf));
}

void ws2812_set_strip(ws2812_strip_t strip, uint8_t r, uint8_t g, uint8_t b)
{
    if (strip >= WS2812_STRIP_NUM) {
        return;
    }
    uint8_t buf[WS2812_LED_MAX * WS2812_GRB_BYTES];
    uint32_t leds = g_strip_leds[strip];
    ws2812_fill_solid(buf, leds, r, g, b);
    ws2812_send_bytes(g_ws2812_strip_bit[strip], buf, leds * WS2812_GRB_BYTES);
}

uint8_t ws2812_strip_len(ws2812_strip_t strip)
{
    return (strip < WS2812_STRIP_NUM) ? g_strip_leds[strip] : 0;
}

void ws2812_strip_frame(ws2812_strip_t strip, const uint8_t *rgb)
{
    if (strip >= WS2812_STRIP_NUM || rgb == NULL) {
        return;
    }
    uint8_t buf[WS2812_LED_MAX * WS2812_GRB_BYTES];
    uint32_t leds = g_strip_leds[strip];
    for (uint32_t led = 0; led < leds; led++) {
        /* RGB -> GRB，同时做伽马校正 */
        buf[led * WS2812_GRB_BYTES + 0] = g_ws2812_gamma[rgb[led * WS2812_GRB_BYTES + 1]];
        buf[led * WS2812_GRB_BYTES + 1] = g_ws2812_gamma[rgb[led * WS2812_GRB_BYTES + 0]];
        buf[led * WS2812_GRB_BYTES + 2] = g_ws2812_gamma[rgb[led * WS2812_GRB_BYTES + 2]];
    }
    ws2812_send_bytes(g_ws2812_strip_bit[strip], buf, leds * WS2812_GRB_BYTES);
}

/* 色相环 0~255 -> RGB（三段线性折线，最大分量 255），亮度另行缩放 */
static void ws2812_hue_wheel(uint8_t pos, uint8_t *r, uint8_t *g, uint8_t *b)
{
    if (pos < 85) {                    /* 红 -> 绿 */
        *r = 255 - pos * 3;
        *g = pos * 3;
        *b = 0;
    } else if (pos < 170) {            /* 绿 -> 蓝 */
        pos -= 85;
        *r = 0;
        *g = 255 - pos * 3;
        *b = pos * 3;
    } else {                           /* 蓝 -> 红 */
        pos -= 170;
        *r = pos * 3;
        *g = 0;
        *b = 255 - pos * 3;
    }
}

/* 开机动画（逐灯帧走 ws2812_strip_frame，自动伽马校正）：
 * 阶段1 彩虹流光扫过：升余弦亮度窗从灯带根部滑到末端，窗内颜色沿色相环渐变，
 *        头尾亮度柔和归零，无生硬边界；三条灯带按各自长度归一化，同时到达末端。
 * 阶段2 冰蓝呼吸：整条以 sin^2 包络亮起再落下，一次呼吸后自然归零熄灭。 */
static void ws2812_boot_animation(void)
{
    uint8_t fb[WS2812_LED_MAX * WS2812_GRB_BYTES];

    /* 阶段1：彩虹流光。窗中心从 -W 走到 1+W，保证首尾都从黑场淡入淡出 */
    for (uint32_t frame = 0; frame < WS2812_BOOT_FRAMES; frame++) {
        float t = (float)frame / (WS2812_BOOT_FRAMES - 1);
        float center = -WS2812_BOOT_WAVE_WIDTH + t * (1.0f + 2.0f * WS2812_BOOT_WAVE_WIDTH);
        for (uint32_t s = 0; s < WS2812_STRIP_NUM; s++) {
            uint32_t leds = g_strip_leds[s];
            for (uint32_t led = 0; led < leds; led++) {
                float x = (leds > 1) ? (float)led / (leds - 1) : 0.0f;
                float d = (x - center) / WS2812_BOOT_WAVE_WIDTH;
                float env = 0.0f;
                if (d > -1.0f && d < 1.0f) {
                    env = 0.5f * (1.0f + cosf(WS2812_PI * d)); /* 升余弦窗 */
                }
                uint8_t r;
                uint8_t g;
                uint8_t b;
                uint8_t hue = (uint8_t)(((uint32_t)(x * 180.0f) + frame * 3U) % WS2812_HUE_SPAN);
                ws2812_hue_wheel(hue, &r, &g, &b);
                fb[led * WS2812_GRB_BYTES + 0] = (uint8_t)(r * env * WS2812_BOOT_LEVEL / 255.0f);
                fb[led * WS2812_GRB_BYTES + 1] = (uint8_t)(g * env * WS2812_BOOT_LEVEL / 255.0f);
                fb[led * WS2812_GRB_BYTES + 2] = (uint8_t)(b * env * WS2812_BOOT_LEVEL / 255.0f);
            }
            ws2812_strip_frame((ws2812_strip_t)s, fb);
        }
        osal_msleep(WS2812_BOOT_FRAME_MS);
    }

    /* 阶段2：冰蓝呼吸一次（sin^2 包络起于 0 终于 0） */
    for (uint32_t frame = 0; frame < WS2812_BOOT_FRAMES; frame++) {
        float phase = sinf(WS2812_PI * (float)frame / (WS2812_BOOT_FRAMES - 1));
        float env = phase * phase;
        uint8_t r = (uint8_t)(40.0f * env * WS2812_BOOT_LEVEL / 255.0f);
        uint8_t g = (uint8_t)(140.0f * env * WS2812_BOOT_LEVEL / 255.0f);
        uint8_t b = (uint8_t)(255.0f * env * WS2812_BOOT_LEVEL / 255.0f);
        for (uint32_t s = 0; s < WS2812_STRIP_NUM; s++) {
            uint32_t leds = g_strip_leds[s];
            for (uint32_t led = 0; led < leds; led++) {
                fb[led * WS2812_GRB_BYTES + 0] = r;
                fb[led * WS2812_GRB_BYTES + 1] = g;
                fb[led * WS2812_GRB_BYTES + 2] = b;
            }
            ws2812_strip_frame((ws2812_strip_t)s, fb);
        }
        osal_msleep(WS2812_BOOT_FRAME_MS);
    }
    ws2812_set_all(0, 0, 0);
}

void ws2812_init(void)
{
    if (g_gpios_regs[WS2812_GPIO_CHANNEL] == 0) {
        osal_printk("ws2812: gpio regs not ready, init failed\r\n");
        return;
    }
    g_ws2812_set_reg = (volatile uint32_t *)(g_gpios_regs[WS2812_GPIO_CHANNEL] + WS2812_DATA_SET_OFF);
    g_ws2812_clr_reg = (volatile uint32_t *)(g_gpios_regs[WS2812_GPIO_CHANNEL] + WS2812_DATA_CLR_OFF);
    g_ws2812_in_reg = (volatile uint32_t *)(g_gpios_regs[WS2812_GPIO_CHANNEL] + WS2812_SW_OUT_OFF);
    g_ws2812_oen_reg = (volatile uint32_t *)(g_gpios_regs[WS2812_GPIO_CHANNEL] + WS2812_SW_OEN_OFF);

    g_ws2812_all_mask = 0;
    for (uint32_t s = 0; s < WS2812_STRIP_NUM; s++) {
        uint8_t pin = g_strip_pins[s];
        uapi_pin_set_mode(pin, HAL_PIO_FUNC_GPIO);
        gpio_select_core(pin, CORES_APPS_CORE);
        uapi_gpio_set_dir(pin, GPIO_DIRECTION_OUTPUT);
        uapi_gpio_set_val(pin, GPIO_LEVEL_LOW);
        g_ws2812_strip_bit[s] = 1U << pin;
        g_ws2812_all_mask |= g_ws2812_strip_bit[s];
    }

    /* 伽马查找表：感知亮度线性化，渐变动画无台阶感 */
    for (uint32_t i = 0; i < 256U; i++) {
        g_ws2812_gamma[i] = (uint8_t)(powf((float)i / 255.0f, WS2812_GAMMA) * 255.0f + 0.5f);
    }

    ws2812_timing_calibrate();
    ws2812_link_check();
    for (uint32_t s = 0; s < WS2812_STRIP_NUM; s++) {
        ws2812_pad_drive_test(g_strip_names[s], g_ws2812_strip_bit[s]);
    }
    /* 开机动画：彩虹流光扫过 + 冰蓝呼吸，约 3.2s 后熄灭进入待机。不依赖 ICM20948 等
     * 其他模块，也顺带肉眼确认三条灯带接线和每颗灯珠都正常（灯珠坏死无法从数据线上检测） */
    ws2812_boot_animation();
    osal_printk("ws2812: init ok, right/left/rear din=gpio%d/%d/%d leds=%d/%d/%d\r\n",
                g_strip_pins[WS2812_STRIP_RIGHT], g_strip_pins[WS2812_STRIP_LEFT],
                g_strip_pins[WS2812_STRIP_REAR], g_strip_leds[WS2812_STRIP_RIGHT],
                g_strip_leds[WS2812_STRIP_LEFT], g_strip_leds[WS2812_STRIP_REAR]);
}
