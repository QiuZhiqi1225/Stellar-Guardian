cloud-integration 目录说明

本目录用于汇总“华为云接入相关”的代码和配置模板。

需要注意：
- 当前仓库中没有一套独立部署在华为云上的应用工程代码。
- 华为云侧主要是 IoTDA 数据转发、SMN/webhook 配置和设备属性上报规则。
- 因此本目录收录的是本项目里与华为云接入直接相关的后端解析代码、配置模板和测试脚本。

主要内容包括：
- huawei.py：解析华为 IoTDA / webhook 消息
- config.py：读取接入相关环境变量
- .env.example：配置模板
- start-public-webhook-test.ps1：公网 webhook 测试脚本
