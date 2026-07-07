/*
 * Copyright (c) HiSilicon (Shanghai) Technologies Co., Ltd. 2023-2023.
 * Licensed under the Apache License, Version 2.0.
 *
 * HLK-LD2450 毫米波雷达驱动，从 radar 样例移植，UART 总线/引脚见 ld2450.h。
 */

#include "ld2450.h"

#include <string.h>
#include "pinctrl.h"
#include "soc_osal.h"
#include "uart.h"

#define LD2450_FRAME_HEADER_0 0xAA
#define LD2450_FRAME_HEADER_1 0xFF
#define LD2450_FRAME_HEADER_2 0x03
#define LD2450_FRAME_HEADER_3 0x00
#define LD2450_FRAME_TAIL_0 0x55
#define LD2450_FRAME_TAIL_1 0xCC
#define LD2450_ONE_TARGET_LEN 8
#define LD2450_TARGET_OFFSET 4

static uint8_t g_ld2450_uart_rx_buffer[LD2450_UART_RX_BUFFER_SIZE] = {0};

static uart_buffer_config_t g_ld2450_uart_buffer_config = {
    .rx_buffer = g_ld2450_uart_rx_buffer,
    .rx_buffer_size = LD2450_UART_RX_BUFFER_SIZE
};

static uint16_t ld2450_read_u16(const uint8_t *data)
{
    return (uint16_t)data[0] | ((uint16_t)data[1] << 8);
}

static int16_t ld2450_decode_signed(uint16_t raw)
{
    if ((raw & 0x8000) != 0) {
        return (int16_t)(raw & 0x7FFF);
    }
    return (int16_t)(0 - (int16_t)raw);
}

static void ld2450_decode_frame(const uint8_t *data, ld2450_frame_t *frame)
{
    for (uint8_t i = 0; i < LD2450_TARGET_NUM; i++) {
        uint8_t offset = LD2450_TARGET_OFFSET + i * LD2450_ONE_TARGET_LEN;
        uint16_t raw_x = ld2450_read_u16(&data[offset]);
        uint16_t raw_y = ld2450_read_u16(&data[offset + 2]);
        uint16_t speed_data = ld2450_read_u16(&data[offset + 4]);
        uint16_t raw_resolution = ld2450_read_u16(&data[offset + 6]);

        frame->target[i].valid = (raw_x != 0 || raw_y != 0 || speed_data != 0 || raw_resolution != 0);
        frame->target[i].x_mm = ld2450_decode_signed(raw_x);
        frame->target[i].y_mm = ld2450_decode_signed(raw_y);
        frame->target[i].speed_cm_s = ld2450_decode_signed(speed_data);
        frame->target[i].distance_resolution_mm = raw_resolution;
    }
}

void Ld2450ParserInit(ld2450_parser_t *parser)
{
    if (parser == NULL) {
        return;
    }
    (void)memset(parser, 0, sizeof(ld2450_parser_t));
}

bool Ld2450ParseByte(ld2450_parser_t *parser, uint8_t byte, ld2450_frame_t *frame)
{
    if (parser == NULL || frame == NULL) {
        return false;
    }

    if (parser->len == 0 && byte != LD2450_FRAME_HEADER_0) {
        return false;
    }

    parser->data[parser->len++] = byte;

    if ((parser->len == 2 && parser->data[1] != LD2450_FRAME_HEADER_1) ||
        (parser->len == 3 && parser->data[2] != LD2450_FRAME_HEADER_2) ||
        (parser->len == 4 && parser->data[3] != LD2450_FRAME_HEADER_3)) {
        parser->len = (byte == LD2450_FRAME_HEADER_0) ? 1 : 0;
        parser->data[0] = byte;
        return false;
    }

    if (parser->len < LD2450_FRAME_LEN) {
        return false;
    }

    bool ok = (parser->data[LD2450_FRAME_LEN - 2] == LD2450_FRAME_TAIL_0 &&
               parser->data[LD2450_FRAME_LEN - 1] == LD2450_FRAME_TAIL_1);
    if (ok) {
        ld2450_decode_frame(parser->data, frame);
    }

    parser->len = 0;
    return ok;
}

static void ld2450_uart_init_pin(void)
{
#if defined(CONFIG_PINCTRL_SUPPORT_IE)
    uapi_pin_set_ie(LD2450_UART_RXD_PIN, PIN_IE_1);
#endif
    uapi_pin_set_mode(LD2450_UART_TXD_PIN, LD2450_UART_PIN_MODE);
    uapi_pin_set_mode(LD2450_UART_RXD_PIN, LD2450_UART_PIN_MODE);
}

errcode_t Ld2450UartInit(void)
{
    uart_attr_t attr = {
        .baud_rate = LD2450_UART_BAUDRATE,
        .data_bits = UART_DATA_BIT_8,
        .stop_bits = UART_STOP_BIT_1,
        .parity = UART_PARITY_NONE
    };

    uart_pin_config_t pin_config = {
        .tx_pin = LD2450_UART_TXD_PIN,
        .rx_pin = LD2450_UART_RXD_PIN,
        .cts_pin = PIN_NONE,
        .rts_pin = PIN_NONE
    };

    ld2450_uart_init_pin();
    uapi_uart_deinit(LD2450_UART_BUS_ID);
    return uapi_uart_init(LD2450_UART_BUS_ID, &pin_config, &attr, NULL, &g_ld2450_uart_buffer_config);
}

int32_t Ld2450UartRead(uint8_t *buffer, uint32_t len)
{
    if (buffer == NULL || len == 0) {
        return 0;
    }
    return uapi_uart_read(LD2450_UART_BUS_ID, buffer, len, 0);
}
