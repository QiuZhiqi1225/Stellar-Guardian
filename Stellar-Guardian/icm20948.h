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
 */

#ifndef ICM20948_H
#define ICM20948_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ICM20948 I2C 从机地址：AD0=0 时为 0x68，AD0=1 时为 0x69，初始化时自动探测 */
#define ICM20948_I2C_ADDR_LOW   0x68
#define ICM20948_I2C_ADDR_HIGH  0x69

/* 内部磁力计 AK09916 I2C 地址（bypass 直通模式下可见） */
#define AK09916_I2C_ADDR        0x0C

/* 9 轴 + 温度数据，均为换算后的物理量 */
typedef struct {
    float accel_g[3];   /* 加速度，单位 g，顺序 X/Y/Z */
    float gyro_dps[3];  /* 角速度，单位 °/s，顺序 X/Y/Z */
    float mag_ut[3];    /* 磁场强度，单位 uT，已对齐到加速度计坐标系 */
    float temp_c;       /* 芯片温度，单位 ℃ */
} icm20948_data_t;

/**
 * @brief 初始化 ICM20948（复位、唤醒、量程配置）并使能内部磁力计 AK09916。
 *        调用前需保证 I2C 总线已初始化。
 * @return 0 成功，其他值失败
 */
int32_t icm20948_init(uint32_t i2c_bus);

/**
 * @brief 读取加速度计、陀螺仪、磁力计和温度数据。
 *        磁力计无新数据时保留上一次的值。
 * @return 0 成功，其他值失败
 */
int32_t icm20948_read_data(icm20948_data_t *data);

#ifdef __cplusplus
}
#endif

#endif /* ICM20948_H */