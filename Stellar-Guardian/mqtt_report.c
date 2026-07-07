/*
 * Copyright (c) HiSilicon (Shanghai) Technologies Co., Ltd. 2023-2023. All rights reserved.
 * Licensed under the Apache License, Version 2.0.
 *
 * 摔倒监测数据 MQTT 云端上报（华为云 IoTDA），从 mqtt 样例移植。
 * 使用前必须替换下方 6 个配置宏（WiFi 账号密码 + 设备接入地址 + 三元组），
 * 并在云端产品模型中创建服务 "fall" 及同名属性，详见 README。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include "MQTTClient.h"
#include "cJSON.h"
#include "soc_osal.h"
#include "osal_debug.h"
#include "common_def.h"
#include "wifi_connect.h"
#include "mqtt_report.h"

/* ======== 以下 6 项按自己的环境修改 ======== */
#define CONFIG_WIFI_SSID  "Citizen的Pura 70"        /* WiFi 热点名称 */
#define CONFIG_WIFI_PWD   "churchillup" /* WiFi 热点密码 */
/* 华为云 IoTDA 设备接入地址（保留 tcp:// 前缀） */
#define MQTT_ADDRESS   "tcp://f8a2d32f03.st1.iotda-device.cn-north-4.myhuaweicloud.com"
/* 由设备 ID + 密钥在 https://iot-tool.obs-website.cn-north-4.myhuaweicloud.com/ 生成 */
#define MQTT_CLIENTID  "6a4b66c17f2e6c302f827a87_qzqwytbjx_0_0_2026070609"
#define MQTT_USERNAME  "6a4b66c17f2e6c302f827a87_qzqwytbjx"
#define MQTT_PASSWORD  "05e0454d5856cdc38108cddaf7797e8c81a0f1078c3ae6f3c99b50038ee1edb8"
/* ========================================== */

#define MQTT_SERVICE_ID       "FALL"    /* 云端产品模型里的服务 ID，须与控制台一字不差（区分大小写） */
#define MQTT_QOS              1
#define MQTT_KEEPALIVE_S      120
#define MQTT_CONNECT_WAIT_MS  1000
#define MQTT_PUBLISH_WAIT_MS  10000L
#define MQTT_RECONNECT_MS     5000
#define MQTT_CHECK_PERIOD_MS  1000      /* 每秒检查一次是否需要上报 */
#define MQTT_REPORT_PERIOD_CNT 3        /* 常规遥测每 3 个检查周期（3s）上报一次 */
#define TOPIC_BUF_LEN         128
#define PERCENT_SCALE         100

fall_report_data_t g_fall_report = {
    .ax_g = 0.0f, .ay_g = 0.0f, .az_g = 1.0f,
    .acc_norm_g = 1.0f, .acc_min_g = 1.0f, .acc_max_g = 1.0f,
    .fall_state = 0, .fall_count = 0,
};

static MQTTClient g_mqtt_client = NULL;
extern int MQTTClient_init(void);

/* 与主文件同款：SDK printf 不支持 %f，手工格式化两位小数 */
static void report_float_to_str(float v, char *buf, uint32_t size)
{
    unused(size);
    int32_t scaled = (int32_t)(v * (float)PERCENT_SCALE);
    const char *sign = (v < 0.0f) ? "-" : "";
    if (scaled < 0) {
        scaled = -scaled;
    }
    if (sprintf(buf, "%s%d.%02d", sign, scaled / PERCENT_SCALE, scaled % PERCENT_SCALE) < 0) {
        buf[0] = '\0';
    }
}

/* 组华为云属性上报 JSON：
 * {"services":[{"service_id":"fall","properties":{...}}]} */
static char *make_fall_json(void)
{
    const char *state_names[] = { "NORMAL", "FREEFALL", "ALARM" };
    uint32_t state = g_fall_report.fall_state;
    if (state > 2) { /* 2:状态枚举上限 */
        state = 0;
    }
    char num[16] = { 0 };

    cJSON *root = cJSON_CreateObject();
    cJSON *services = cJSON_CreateArray();
    cJSON *service = cJSON_CreateObject();
    cJSON *properties = cJSON_CreateObject();
    if (root == NULL || services == NULL || service == NULL || properties == NULL) {
        cJSON_Delete(root);
        cJSON_Delete(services);
        cJSON_Delete(service);
        cJSON_Delete(properties);
        return NULL;
    }
    cJSON_AddStringToObject(service, "service_id", MQTT_SERVICE_ID);

    report_float_to_str(g_fall_report.acc_norm_g, num, sizeof(num));
    cJSON_AddStringToObject(properties, "accel", num);
    cJSON_AddStringToObject(properties, "state", state_names[state]);
    cJSON_AddNumberToObject(properties, "fall_count", (double)g_fall_report.fall_count);

    cJSON_AddItemToObject(service, "properties", properties);
    cJSON_AddItemToArray(services, service);
    cJSON_AddItemToObject(root, "services", services);

    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return json;
}

static int mqtt_connect_broker(void)
{
    MQTTClient_connectOptions conn_opts = MQTTClient_connectOptions_initializer;
    conn_opts.keepAliveInterval = MQTT_KEEPALIVE_S;
    conn_opts.cleansession = 1;
    conn_opts.username = MQTT_USERNAME;
    conn_opts.password = MQTT_PASSWORD;

    if (g_mqtt_client == NULL) {
        MQTTClient_init();
        MQTTClient_create(&g_mqtt_client, MQTT_ADDRESS, MQTT_CLIENTID, MQTTCLIENT_PERSISTENCE_NONE, NULL);
    }
    int rc = MQTTClient_connect(g_mqtt_client, &conn_opts);
    if (rc != MQTTCLIENT_SUCCESS) {
        osal_printk("mqtt: connect failed, rc=%d\r\n", rc);
        return -1;
    }
    osal_printk("mqtt: connect success\r\n");
    return 0;
}

static int mqtt_publish_report(const char *topic)
{
    MQTTClient_message pubmsg = MQTTClient_message_initializer;
    MQTTClient_deliveryToken token = 0;
    char *payload = make_fall_json();
    if (payload == NULL) {
        return -1;
    }
    pubmsg.payload = payload;
    pubmsg.payloadlen = (int)strlen(payload);
    pubmsg.qos = MQTT_QOS;
    pubmsg.retained = 0;
    int rc = MQTTClient_publishMessage(g_mqtt_client, topic, &pubmsg, &token);
    if (rc == MQTTCLIENT_SUCCESS) {
        rc = MQTTClient_waitForCompletion(g_mqtt_client, token, MQTT_PUBLISH_WAIT_MS);
    }
    if (rc == MQTTCLIENT_SUCCESS) {
        osal_printk("mqtt: report ok %s\r\n", payload);
    } else {
        osal_printk("mqtt: publish failed, rc=%d\r\n", rc);
    }
    cJSON_free(payload);
    return (rc == MQTTCLIENT_SUCCESS) ? 0 : -1;
}

void mqtt_report_task(void)
{
    static char topic[TOPIC_BUF_LEN];
    if (snprintf(topic, sizeof(topic), "$oc/devices/%s/sys/properties/report", MQTT_USERNAME) <= 0) {
        return;
    }

    wifi_connect(CONFIG_WIFI_SSID, CONFIG_WIFI_PWD);
    while (mqtt_connect_broker() != 0) {
        osal_msleep(MQTT_RECONNECT_MS);
    }
    osal_msleep(MQTT_CONNECT_WAIT_MS); /* 等连接稳定 */

    uint32_t period_cnt = 0;
    uint32_t last_fall_count = g_fall_report.fall_count;
    while (1) {
        osal_msleep(MQTT_CHECK_PERIOD_MS);

        /* 常规遥测按周期上报；确认摔倒（计数变化）时不等周期立即上报 */
        bool fall_event = (g_fall_report.fall_count != last_fall_count);
        if (!fall_event && ++period_cnt < MQTT_REPORT_PERIOD_CNT) {
            continue;
        }
        period_cnt = 0;

        if (mqtt_publish_report(topic) == 0) {
            last_fall_count = g_fall_report.fall_count;
            /* 极值窗口随上报复位，下个周期重新累计 */
            g_fall_report.acc_min_g = g_fall_report.acc_norm_g;
            g_fall_report.acc_max_g = g_fall_report.acc_norm_g;
        } else {
            /* 发布失败按掉线处理：断开重连后下轮重试 */
            MQTTClient_disconnect(g_mqtt_client, MQTT_PUBLISH_WAIT_MS);
            osal_msleep(MQTT_RECONNECT_MS);
            (void)mqtt_connect_broker();
        }
    }
}
