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
 * ICM20948 九轴姿态传感器精简驱动（I2C 模式）。
 * 寄存器定义参考 InvenSense DS-000189《ICM-20948 Datasheet》v1.3。
 * 磁力计 AK09916 通过芯片内部 I2C 主机代理访问（SparkFun 官方库同款方案）。
 */

#include <stdbool.h>
#include "i2c.h"
#include "soc_osal.h"
#include "osal_debug.h"
#include "icm20948.h"

/* 通用寄存器（所有 Bank 可见） */
#define REG_BANK_SEL            0x7F    /* bit[5:4] 选择寄存器 Bank */

/* Bank 0 寄存器 */
#define REG_WHO_AM_I            0x00    /* 固定值 0xEA */
#define REG_USER_CTRL           0x03
#define REG_PWR_MGMT_1          0x06
#define REG_INT_PIN_CFG         0x0F
#define REG_I2C_MST_STATUS      0x17    /* bit6 SLV4_DONE */
#define REG_ACCEL_XOUT_H        0x2D    /* 加速度 6B + 陀螺仪 6B + 温度 2B 连续 */
#define REG_EXT_SLV_SENS_DATA_00 0x3B   /* 内部 I2C 主机代理读取的磁力计数据 */

/* Bank 2 寄存器 */
#define REG_GYRO_SMPLRT_DIV     0x00
#define REG_GYRO_CONFIG_1       0x01
#define REG_ODR_ALIGN_EN        0x09
#define REG_ACCEL_SMPLRT_DIV_2  0x11
#define REG_ACCEL_CONFIG        0x14

/* Bank 3 寄存器（内部 I2C 主机） */
#define REG_I2C_MST_CTRL        0x01
#define REG_I2C_SLV0_ADDR       0x03
#define REG_I2C_SLV0_REG        0x04
#define REG_I2C_SLV0_CTRL       0x05
#define REG_I2C_SLV4_ADDR       0x13
#define REG_I2C_SLV4_REG        0x14
#define REG_I2C_SLV4_CTRL       0x15
#define REG_I2C_SLV4_DO         0x16
#define REG_I2C_SLV4_DI         0x17

/* AK09916 磁力计寄存器 */
#define AK09916_REG_WIA2        0x01    /* 固定值 0x09 */
#define AK09916_REG_ST1         0x10    /* bit0 DRDY */
#define AK09916_REG_CNTL2       0x31
#define AK09916_REG_CNTL3       0x32

#define ICM20948_WHO_AM_I_VAL   0xEA
#define AK09916_WIA2_VAL        0x09

#define ICM20948_RESET          0x80    /* PWR_MGMT_1: DEVICE_RESET */
#define ICM20948_CLKSEL_AUTO    0x01    /* PWR_MGMT_1: 自动选择最优时钟 */
#define ICM20948_I2C_MST_EN     0x20    /* USER_CTRL: 使能内部 I2C 主机 */
#define ICM20948_I2C_MST_RST    0x02    /* USER_CTRL: 复位内部 I2C 主机（自清零） */
#define I2C_MST_CLK_345KHZ      0x07    /* I2C_MST_CTRL: 内部 I2C 主机时钟，手册推荐值 */
#define I2C_MST_P_NSR           0x10    /* I2C_MST_CTRL: 读从机时发停止位，AK09916 需要 */
#define I2C_SLV_EN              0x80    /* I2C_SLVx_CTRL: 使能该从机通道 */
#define I2C_SLV_READ            0x80    /* I2C_SLVx_ADDR: 读方向标志 */
#define I2C_MST_SLV4_DONE       0x40    /* I2C_MST_STATUS: SLV4 传输完成 */

/* GYRO_CONFIG_1: DLPFCFG=3 | FS_SEL=1(±500dps) | FCHOICE=1(使能低通) */
#define GYRO_CONFIG_500DPS_DLPF 0x1B
/* ACCEL_CONFIG: DLPFCFG=3 | FS_SEL=2(±8g) | FCHOICE=1(使能低通)，±8g 保证摔倒撞击不削顶 */
#define ACCEL_CONFIG_8G_DLPF    0x1D
#define SMPLRT_DIV_10           10      /* 采样率 1125/(1+10) ≈ 102Hz */

#define AK09916_MODE_100HZ      0x08    /* CNTL2: 连续测量模式 4，100Hz */
#define AK09916_SOFT_RESET      0x01    /* CNTL3: SRST */
#define AK09916_ST1_DRDY        0x01

#define GYRO_LSB_PER_DPS        65.5f   /* ±500dps 量程灵敏度 */
#define ACCEL_LSB_PER_G         4096.0f /* ±8g 量程灵敏度 */
#define MAG_UT_PER_LSB          0.15f   /* AK09916 固定灵敏度 */
#define TEMP_LSB_PER_C          333.87f
#define TEMP_OFFSET_C           21.0f

#define ICM20948_RESET_DELAY_MS 100
#define ICM20948_WAKE_DELAY_MS  20
#define MAG_INIT_RETRY_MAX      5       /* 磁力计首次探测常失败，需复位 I2C 主机重试 */
#define BYTE_BITS               8
#define ACCEL_GYRO_TEMP_LEN     14      /* 6B accel + 6B gyro + 2B temp */
#define MAG_DATA_LEN            9       /* ST1 + 6B data + TMPS + ST2，必须含 ST2 才算完成一次测量 */

static uint32_t g_icm_bus = 1;
static uint16_t g_icm_addr = ICM20948_I2C_ADDR_LOW;
static uint8_t g_icm_cur_bank = 0xFF;

/* 向指定 I2C 设备的寄存器写一个字节 */
static int32_t icm_i2c_write_reg(uint16_t dev_addr, uint8_t reg, uint8_t value)
{
    uint8_t buf[] = { reg, value };
    i2c_data_t data = { 0 };
    data.send_buf = buf;
    data.send_len = sizeof(buf);
    return (int32_t)uapi_i2c_master_write(g_icm_bus, dev_addr, &data);
}

/* 从指定 I2C 设备的寄存器连续读多个字节 */
static int32_t icm_i2c_read_regs(uint16_t dev_addr, uint8_t reg, uint8_t *buf, uint32_t len)
{
    uint8_t reg_buf[] = { reg };
    i2c_data_t data = { 0 };
    data.send_buf = reg_buf;
    data.send_len = sizeof(reg_buf);
    int32_t ret = (int32_t)uapi_i2c_master_write(g_icm_bus, dev_addr, &data);
    if (ret != 0) {
        return ret;
    }
    data.receive_buf = buf;
    data.receive_len = len;
    return (int32_t)uapi_i2c_master_read(g_icm_bus, dev_addr, &data);
}

/* 切换 ICM20948 寄存器 Bank（0~3），相同 Bank 不重复写 */
static int32_t icm_select_bank(uint8_t bank)
{
    if (bank == g_icm_cur_bank) {
        return 0;
    }
    int32_t ret = icm_i2c_write_reg(g_icm_addr, REG_BANK_SEL, (uint8_t)(bank << 4));
    if (ret == 0) {
        g_icm_cur_bank = bank;
    }
    return ret;
}

static int32_t icm_write_reg(uint8_t bank, uint8_t reg, uint8_t value)
{
    int32_t ret = icm_select_bank(bank);
    if (ret != 0) {
        return ret;
    }
    return icm_i2c_write_reg(g_icm_addr, reg, value);
}

static int32_t icm_read_regs(uint8_t bank, uint8_t reg, uint8_t *buf, uint32_t len)
{
    int32_t ret = icm_select_bank(bank);
    if (ret != 0) {
        return ret;
    }
    return icm_i2c_read_regs(g_icm_addr, reg, buf, len);
}

/* 扫描 I2C 总线，打印所有应答的从机地址，用于接线诊断 */
static void icm_i2c_bus_scan(void)
{
    uint32_t found = 0;
    osal_printk("icm20948: scanning i2c bus %d ...\r\n", g_icm_bus);
    for (uint16_t addr = 0x08; addr <= 0x77; addr++) {
        uint8_t tmp = 0;
        i2c_data_t data = { 0 };
        data.receive_buf = &tmp;
        data.receive_len = 1;
        if (uapi_i2c_master_read(g_icm_bus, addr, &data) == ERRCODE_SUCC) {
            osal_printk("icm20948: device found at addr 0x%x\r\n", addr);
            found++;
        }
    }
    if (found == 0) {
        osal_printk("icm20948: no device on bus, check SCL/SDA/VCC wiring!\r\n");
    }
}

/* 探测器件地址：依次尝试 0x68/0x69，通过 WHO_AM_I 判别 */
static int32_t icm_probe(void)
{
    const uint16_t addrs[] = { ICM20948_I2C_ADDR_LOW, ICM20948_I2C_ADDR_HIGH };
    for (uint32_t i = 0; i < sizeof(addrs) / sizeof(addrs[0]); i++) {
        g_icm_addr = addrs[i];
        g_icm_cur_bank = 0xFF;
        uint8_t who = 0;
        int32_t ret = icm_read_regs(0, REG_WHO_AM_I, &who, 1);
        if (ret == 0 && who == ICM20948_WHO_AM_I_VAL) {
            osal_printk("icm20948: found at addr 0x%x\r\n", g_icm_addr);
            return 0;
        }
        osal_printk("icm20948: addr 0x%x ret=0x%x who=0x%x\r\n", g_icm_addr, ret, who);
    }
    osal_printk("icm20948: probe failed, check wiring!\r\n");
    icm_i2c_bus_scan();
    return -1;
}

/* 通过内部 I2C 主机的 SLV4 通道对 AK09916 做单寄存器读写。
 * write 为真时写入 value；为假时读取结果存入 *value。 */
static int32_t ak09916_transact(uint8_t reg, uint8_t *value, bool write)
{
    uint8_t addr = write ? AK09916_I2C_ADDR : (AK09916_I2C_ADDR | I2C_SLV_READ);
    int32_t ret = icm_write_reg(3, REG_I2C_SLV4_ADDR, addr);
    ret |= icm_write_reg(3, REG_I2C_SLV4_REG, reg);
    if (write) {
        ret |= icm_write_reg(3, REG_I2C_SLV4_DO, *value);
    }
    ret |= icm_write_reg(3, REG_I2C_SLV4_CTRL, I2C_SLV_EN); /* 触发一次传输 */
    if (ret != 0) {
        return ret;
    }
    for (uint32_t i = 0; i < 20; i++) { /* 最多等 20 次 */
        uint8_t status = 0;
        ret = icm_read_regs(0, REG_I2C_MST_STATUS, &status, 1);
        if (ret == 0 && (status & I2C_MST_SLV4_DONE) != 0) {
            if (!write) {
                return icm_read_regs(3, REG_I2C_SLV4_DI, value, 1);
            }
            return 0;
        }
        osal_msleep(2); /* 2ms 轮询间隔 */
    }
    return -1;
}

static int32_t ak09916_write(uint8_t reg, uint8_t value)
{
    return ak09916_transact(reg, &value, true);
}

static int32_t ak09916_read(uint8_t reg, uint8_t *value)
{
    return ak09916_transact(reg, value, false);
}

static int32_t ak09916_init(void)
{
    uint8_t wia2 = 0;
    int32_t ret = ak09916_read(AK09916_REG_WIA2, &wia2);
    if (ret != 0 || wia2 != AK09916_WIA2_VAL) {
        osal_printk("icm20948: ak09916 not found (wia2=0x%x)\r\n", wia2);
        return -1;
    }
    ret = ak09916_write(AK09916_REG_CNTL3, AK09916_SOFT_RESET);
    if (ret != 0) {
        return ret;
    }
    osal_msleep(ICM20948_WAKE_DELAY_MS);
    ret = ak09916_write(AK09916_REG_CNTL2, AK09916_MODE_100HZ);
    if (ret != 0) {
        return ret;
    }
    /* 配置 SLV0 周期性读取 ST1~ST2 共 9 字节，数据自动映射到 EXT_SLV_SENS_DATA_00 */
    ret = icm_write_reg(3, REG_I2C_SLV0_ADDR, AK09916_I2C_ADDR | I2C_SLV_READ);
    ret |= icm_write_reg(3, REG_I2C_SLV0_REG, AK09916_REG_ST1);
    ret |= icm_write_reg(3, REG_I2C_SLV0_CTRL, I2C_SLV_EN | MAG_DATA_LEN);
    return ret;
}

int32_t icm20948_init(uint32_t i2c_bus)
{
    g_icm_bus = i2c_bus;
    if (icm_probe() != 0) {
        return -1;
    }

    /* 复位后重新探测 Bank 状态 */
    int32_t ret = icm_write_reg(0, REG_PWR_MGMT_1, ICM20948_RESET);
    if (ret != 0) {
        return ret;
    }
    osal_msleep(ICM20948_RESET_DELAY_MS);
    g_icm_cur_bank = 0xFF;

    /* 退出睡眠并选择自动时钟 */
    ret = icm_write_reg(0, REG_PWR_MGMT_1, ICM20948_CLKSEL_AUTO);
    if (ret != 0) {
        return ret;
    }
    osal_msleep(ICM20948_WAKE_DELAY_MS);

    /* 陀螺仪与加速度计量程、低通滤波、采样率配置 */
    ret = icm_write_reg(2, REG_GYRO_CONFIG_1, GYRO_CONFIG_500DPS_DLPF);
    ret |= icm_write_reg(2, REG_GYRO_SMPLRT_DIV, SMPLRT_DIV_10);
    ret |= icm_write_reg(2, REG_ACCEL_CONFIG, ACCEL_CONFIG_8G_DLPF);
    ret |= icm_write_reg(2, REG_ACCEL_SMPLRT_DIV_2, SMPLRT_DIV_10);
    ret |= icm_write_reg(2, REG_ODR_ALIGN_EN, 1);
    if (ret != 0) {
        return ret;
    }

    /* 使能内部 I2C 主机，由它代理访问内部磁力计 AK09916 */
    ret = icm_write_reg(3, REG_I2C_MST_CTRL, I2C_MST_CLK_345KHZ | I2C_MST_P_NSR);
    ret |= icm_write_reg(0, REG_USER_CTRL, ICM20948_I2C_MST_EN);
    if (ret != 0) {
        return ret;
    }
    osal_msleep(ICM20948_WAKE_DELAY_MS);

    /* 磁力计首次探测常失败（SparkFun 库同款问题），失败则复位 I2C 主机后重试 */
    bool mag_ok = false;
    for (uint32_t retry = 0; retry < MAG_INIT_RETRY_MAX; retry++) {
        if (retry > 0) {
            (void)icm_write_reg(0, REG_USER_CTRL, ICM20948_I2C_MST_EN | ICM20948_I2C_MST_RST);
            osal_msleep(ICM20948_WAKE_DELAY_MS);
            (void)icm_write_reg(0, REG_USER_CTRL, ICM20948_I2C_MST_EN);
            osal_msleep(ICM20948_WAKE_DELAY_MS);
        }
        if (ak09916_init() == 0) {
            mag_ok = true;
            break;
        }
    }
    if (!mag_ok) {
        osal_printk("icm20948: mag init failed, continue without mag\r\n");
    }
    return 0;
}

static int16_t to_int16(uint8_t high, uint8_t low)
{
    return (int16_t)(((uint16_t)high << BYTE_BITS) | low);
}

int32_t icm20948_read_data(icm20948_data_t *data)
{
    if (data == NULL) {
        return -1;
    }

    uint8_t buf[ACCEL_GYRO_TEMP_LEN] = { 0 };
    int32_t ret = icm_read_regs(0, REG_ACCEL_XOUT_H, buf, sizeof(buf));
    if (ret != 0) {
        osal_printk("icm20948: accel/gyro read err=0x%x\r\n", ret);
        g_icm_cur_bank = 0xFF; /* 读失败后不再信任 bank 缓存，下次强制重选 */
        return ret;
    }
    for (uint32_t i = 0; i < 3; i++) { /* 3 轴 */
        data->accel_g[i] = to_int16(buf[i * 2], buf[i * 2 + 1]) / ACCEL_LSB_PER_G;
        data->gyro_dps[i] = to_int16(buf[6 + i * 2], buf[6 + i * 2 + 1]) / GYRO_LSB_PER_DPS; /* 陀螺仪数据偏移6B */
    }
    data->temp_c = to_int16(buf[12], buf[13]) / TEMP_LSB_PER_C + TEMP_OFFSET_C; /* 温度数据偏移12B */

    /* 磁力计：内部 I2C 主机代理读取的数据已映射到 EXT_SLV_SENS_DATA，仅在 DRDY 置位时更新 */
    uint8_t mag_buf[MAG_DATA_LEN] = { 0 };
    if (icm_read_regs(0, REG_EXT_SLV_SENS_DATA_00, mag_buf, sizeof(mag_buf)) == 0 &&
        (mag_buf[0] & AK09916_ST1_DRDY) != 0) {
        /* AK09916 数据为小端；其 X/Y 轴与加速度计坐标系互换、Z 轴反向，此处统一对齐 */
        float mx = to_int16(mag_buf[2], mag_buf[1]) * MAG_UT_PER_LSB;
        float my = to_int16(mag_buf[4], mag_buf[3]) * MAG_UT_PER_LSB;
        float mz = to_int16(mag_buf[6], mag_buf[5]) * MAG_UT_PER_LSB;
        data->mag_ut[0] = my;
        data->mag_ut[1] = mx;
        data->mag_ut[2] = -mz;
    }
    return 0;
}
