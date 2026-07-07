# ICM20948 摔倒监测 + HLK-LD2450 雷达逼近预警样例

## 功能

- ICM20948（I2C1）：读取加速度计/陀螺仪/磁力计/温度，互补滤波解算姿态角，
  摔倒检测（失重->撞击）触发声光报警，每 500ms 串口打印一次。
- 蜂鸣器音效（与灯效同一节奏源，音量按占空比分级）：开机动画结束时播放
  C6-E6-G6 三连升调迎宾音；摔倒报警为高低双音啁啾（2489/1865Hz，与红色双闪
  同步，最响）；雷达来车为短促单音提示（E6，每脉冲周期 60ms，中等音量）；
  偏头转向为每轮流水起始的轻"嗒"声（1kHz 40ms，最轻，似转向灯继电器）。
- HLK-LD2450 雷达（UART2）：解析目标 x/y/速度，检测到目标快速逼近
  （正前方 ±1m、0.1~2.5m 内、两帧间 y 减小 ≥60mm 且速度 ≥50cm/s）时
  蜂鸣器断续报警 1.2s。摔倒报警优先级更高；按键均可消音。
- OLED：上电显示竞赛 logo 开机画面（树形图案 + "嵌入式芯片与系统设计竞赛"，
  见 logo_splash.h，由脚本从 logo 图生成）；运行时四区布局只保留关键信息——
  顶部大字状态（摔倒 > 雷达来车 > 转向 > NORMAL）+ 分隔线 + 加速度模值/摔倒
  次数 + 雷达距离速度 + 底部提示行；摔倒报警时整屏反色（白底黑字）。
- WS2812 灯带 ×3（右=GPIO0 20颗、左=GPIO2 19颗、后=GPIO1 11颗），全部灯效经
  伽马 2.2 校正（渐变无台阶感）：
  - 开机动画（约 3.2s）：彩虹流光扫过（升余弦亮度窗滑过灯带，头尾柔和淡入淡出）
    → 冰蓝呼吸一次 → 自然归零熄灭；待机灯全灭。
  - 摔倒报警（最高优先级）：三条红色**应急双闪**（80ms×2 连闪 + 暗场，急救车
    节奏），持续到按键消音。
  - 雷达逼近报警：后灯带红色**心跳脉冲**（快攻起缓衰减，640ms 周期，1.2s）。
  - 偏头转向（加速度计 ZY 平面倾角 atan2(ay,az)，>40° 触发、<20° 回正、带迟滞
    和低通）：对应侧灯带**琥珀流水**（车规动态转向灯：根部逐颗点亮扫出 400ms +
    熄灭 160ms 循环）；方向反了时对调 icm20948_demo.c 里两处倾角比较的正负号。

## 接线（WS63 核心板）

| GY-ICM20948V2 模块 | WS63 主板       |
| ------------------ | --------------- |
| VCC                | 3.3V            |
| GND                | GND             |
| SCL                | GPIO16（I2C1_SCL，复用模式 2）|
| SDA                | GPIO15（I2C1_SDA，复用模式 2）|

其余引脚（EDA/ECL/AD0/INT/NCS/FSYNC）悬空即可。
模块上 AD0 拨码开关决定 I2C 地址（0x68 / 0x69），程序会自动探测，无需关心其位置。
OLED（SSD1306）与 ICM20948 并联在同一条 I2C1 总线上（地址 0x3C / 0x68 不冲突）。

| HLK-LD2450 雷达 | WS63 主板       |
| --------------- | --------------- |
| VCC (5V)        | 5V              |
| GND             | GND             |
| TX              | GPIO7（UART2_RXD，复用模式 2）|
| RX              | GPIO8（UART2_TXD，复用模式 2）|

注意：UART1 只能复用在 GPIO15/16 上，与 I2C1 完全重叠，因此雷达必须走
UART2（GPIO7/8），这也是本样例与独立 radar 样例接线不同的原因。
蜂鸣器接 GPIO9，消音按键为板载 USER 键（GPIO13）或扩展板按键（GPIO14）。

| WS2812 灯条 ×3（BTF-5V-WS2812E） | WS63 主板 |
| -------------------------------- | --------- |
| 5V（红线，三条并联）             | 5V        |
| GND（白线，三条并联）            | GND       |
| 右灯带 DIN（绿线，箭头指向远端） | GPIO0（20 颗）|
| 左灯带 DIN                       | GPIO2（19 颗）|
| 后灯带 DIN                       | GPIO1（11 颗）|

灯条每 10mm 一颗灯珠可剪，各条灯珠数在 `ws2812.c` 的 `g_strip_leds` 中配置。
注意方向：数据只能从 DIN 端灌入，接线要接箭头起始端。数据脚是 3.3V 电平驱动
5V 灯条，短杜邦线直连一般可靠；若首灯不亮或颜色错乱，在灯条 5V 供电上串一个
普通二极管（降到 ~4.3V）即可满足 0.7*VDD 的输入高电平门限。

上电时固件会对 DIN 做一次连接自检（上/下拉稳态 + 线路电容充电时间），串口输出：
`link check FAIL ... driven high` = 接错到别的输出脚；`FAIL ... stuck low` =
数据线对地短路或灯条 5V 没接；`WARN ... looks unconnected` = 疑似数据线悬空没接；
`OK ... wiring detected` = 检测到接线。电容法区分悬空/已接线是启发式判断，
`link rise avg=` 行打印原始测量值，误判时按该值调整 ws2812.c 的 WS2812_FLOAT_POLL_MAX。

## MQTT 云端上报（华为云 IoTDA）

摔倒相关数据每 3s 上报一次；确认摔倒时立即上报。上报属性（服务 ID `FALL`，
须与代码 `mqtt_report.c` 里的 MQTT_SERVICE_ID 一字不差）：

| 属性 | 类型 | 含义 |
| ---- | ---- | ---- |
| accel | string | 当前加速度模值 \|a\|（g） |
| state | string | NORMAL / FREEFALL / ALARM |
| fall_count | int | 开机以来累计摔倒次数 |

使用步骤（云端建产品/设备流程详见 mqtt 样例 README 步骤 1~12）：

1. 云端产品模型中新增服务 `FALL`，按上表添加属性（string 类型长度 16、可读；fall_count 用 int）。
2. 修改 `mqtt_report.c` 顶部 6 个宏：WiFi 账号密码（CONFIG_WIFI_SSID/PWD）、
   设备接入地址（MQTT_ADDRESS）、三元组（MQTT_CLIENTID/USERNAME/PASSWORD，
   由设备 ID+密钥在 https://iot-tool.obs-website.cn-north-4.myhuaweicloud.com/ 生成）。
3. 编译烧录后，串口出现 `mqtt: connect success` 与 `mqtt: report ok {...}`，
   云端设备详情页即可看到实时属性。

## 使能编译

在 menuconfig（`ws63_liteos_app.config`）中打开：

```
CONFIG_ENABLE_PERIPHERAL_SAMPLE=y
CONFIG_SAMPLE_SUPPORT_ICM20948=y
```

## 预期串口输出

```
icm20948: found at addr 0x68
icm20948: init success!
accel[g]: 0.012 -0.008 1.002 | gyro[dps]: 0.15 -0.21 0.08
mag[uT]: 23.40 -10.05 -38.70 | temp[C]: 28.35
attitude: pitch=0.52 roll=-0.31 yaw=156.20
```

静止平放时加速度 Z 轴应约为 1g，Pitch/Roll 接近 0°；转动模块可看到姿态角变化。