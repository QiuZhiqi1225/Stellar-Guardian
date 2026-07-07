/*
 * Copyright (c) HiSilicon (Shanghai) Technologies Co., Ltd. 2023-2023.
 * Licensed under the Apache License, Version 2.0.
 *
 * HLK-LD2450 毫米波雷达驱动（UART 帧解析）。
 * 本工程中 GPIO15/16 已被 I2C1（ICM20948+OLED）占用，而 UART1 只能复用在
 * GPIO15/16 上，故雷达改用 UART2：RX=GPIO7、TX=GPIO8（复用模式 2）。
 */

#ifndef LD2450_H
#define LD2450_H

#include <stdbool.h>
#include <stdint.h>
#include "errcode.h"

#ifdef __cplusplus
extern "C" {
#endif

#define LD2450_TARGET_NUM 3
#define LD2450_FRAME_LEN 30

#ifndef LD2450_UART_BAUDRATE
#define LD2450_UART_BAUDRATE 256000
#endif

#ifndef LD2450_UART_BUS_ID
#define LD2450_UART_BUS_ID 2
#endif

#ifndef LD2450_UART_TXD_PIN
#define LD2450_UART_TXD_PIN 8   /* GPIO8 复用为 UART2_TXD */
#endif

#ifndef LD2450_UART_RXD_PIN
#define LD2450_UART_RXD_PIN 7   /* GPIO7 复用为 UART2_RXD */
#endif

#ifndef LD2450_UART_PIN_MODE
#define LD2450_UART_PIN_MODE 2  /* GPIO7/8 的 UART2 复用模式为 2 */
#endif

#ifndef LD2450_UART_RX_BUFFER_SIZE
#define LD2450_UART_RX_BUFFER_SIZE 64
#endif

typedef struct {
    bool valid;
    int16_t x_mm;
    int16_t y_mm;
    int16_t speed_cm_s;
    uint16_t distance_resolution_mm;
} ld2450_target_t;

typedef struct {
    ld2450_target_t target[LD2450_TARGET_NUM];
} ld2450_frame_t;

typedef struct {
    uint8_t data[LD2450_FRAME_LEN];
    uint8_t len;
} ld2450_parser_t;

void Ld2450ParserInit(ld2450_parser_t *parser);
bool Ld2450ParseByte(ld2450_parser_t *parser, uint8_t byte, ld2450_frame_t *frame);
errcode_t Ld2450UartInit(void);
int32_t Ld2450UartRead(uint8_t *buffer, uint32_t len);

#ifdef __cplusplus
}
#endif

#endif
