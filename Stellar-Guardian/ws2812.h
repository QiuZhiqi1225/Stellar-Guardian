/*
 * Copyright (c) HiSilicon (Shanghai) Technologies Co., Ltd. 2023-2023. All rights reserved.
 * Licensed under the Apache License, Version 2.0.
 *
 * WS2812E 灯条驱动（BTF-5V-RGBIC-WS2812E-100L，单线 800kHz 协议，GPIO 位拼时序）。
 * 三条灯带：右=GPIO0(20颗)、左=GPIO2(19颗)、后=GPIO1(11颗)。接线：各灯带 DIN ->
 * 对应 GPIO，5V -> 5V，GND -> GND（数据脚 3.3V 电平，短线直连一般可靠；若首灯不亮/
 * 闪烁异常，在灯条 5V 供电上串一个二极管降到 ~4.3V 即可满足 0.7*VDD 的输入门限）。
 */
#ifndef WS2812_H
#define WS2812_H

#include <stdint.h>

/* 三条灯带编号。对应引脚与灯珠数在 ws2812.c 的 g_strip_pins / g_strip_leds 中定义，
 * 引脚只支持 GPIO0~7（channel0） */
typedef enum {
    WS2812_STRIP_RIGHT = 0, /* 右灯带 DIN = GPIO0，20 颗 */
    WS2812_STRIP_LEFT,      /* 左灯带 DIN = GPIO2，19 颗 */
    WS2812_STRIP_REAR,      /* 后灯带 DIN = GPIO1，11 颗 */
    WS2812_STRIP_NUM,
} ws2812_strip_t;

#define WS2812_LED_MAX  20  /* 三条灯带中最长的灯珠数（帧缓冲大小） */

/* 初始化三条灯带的数据脚并校准位时序延时，播放开机动画（彩虹流光扫过 + 冰蓝呼吸）后熄灭待机 */
void ws2812_init(void);

/* 该条灯带的灯珠数 */
uint8_t ws2812_strip_len(ws2812_strip_t strip);

/* 发送一帧逐灯颜色：rgb 为 R,G,B 连续三元组，长度 = 该条灯珠数 x3。
 * 内部做伽马 2.2 校正（渐变无台阶感），发送期间关中断，耗时同 ws2812_set_strip */
void ws2812_strip_frame(ws2812_strip_t strip, const uint8_t *rgb);

/* 三条灯带同时设为同一颜色并立即发送（r/g/b 0~255，全 0 = 熄灭）。
 * 一次发帧同时驱动三个引脚，耗时与单条相同：发送期间关中断，
 * 每颗灯约 30us（按最长的 20 颗算约 600us），不要在中断上下文调用。 */
void ws2812_set_all(uint8_t r, uint8_t g, uint8_t b);

/* 单独控制一条灯带（其余灯带保持原显示不变），耗时同 ws2812_set_all */
void ws2812_set_strip(ws2812_strip_t strip, uint8_t r, uint8_t g, uint8_t b);

/* 数据脚连接自检（init 时自动执行一次，也可随时调用）：逐条检测外部驱动/对地短路/
 * 疑似未接线，结论以 "ws2812: <strip> link check ..." 从串口输出，见 ws2812.c 注释 */
void ws2812_link_check(void);

#endif
