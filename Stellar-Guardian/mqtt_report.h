/*
 * Copyright (c) HiSilicon (Shanghai) Technologies Co., Ltd. 2023-2023. All rights reserved.
 * Licensed under the Apache License, Version 2.0.
 *
 * 摔倒监测数据 MQTT 云端上报（华为云 IoTDA），从 mqtt 样例移植。
 */

#ifndef MQTT_REPORT_H
#define MQTT_REPORT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* 主任务(50Hz)写入、MQTT 任务读取的共享上报数据。
 * 32 位对齐的 float/uint32 读写在本平台是原子的，无需加锁。 */
typedef struct {
    volatile float ax_g;            /* 三轴加速度 [g] */
    volatile float ay_g;
    volatile float az_g;
    volatile float acc_norm_g;      /* 加速度模值 |a| [g] */
    volatile float acc_min_g;       /* 上次上报以来 |a| 谷值（失重证据） */
    volatile float acc_max_g;       /* 上次上报以来 |a| 峰值（撞击证据） */
    volatile uint32_t fall_state;   /* 摔倒状态机：0=NORMAL 1=FREEFALL 2=ALARM */
    volatile uint32_t fall_count;   /* 开机以来确认摔倒的累计次数 */
} fall_report_data_t;

extern fall_report_data_t g_fall_report;

/* MQTT 上报任务入口：连 WiFi -> 连华为云 -> 周期上报，摔倒时立即上报 */
void mqtt_report_task(void);

#ifdef __cplusplus
}
#endif

#endif
