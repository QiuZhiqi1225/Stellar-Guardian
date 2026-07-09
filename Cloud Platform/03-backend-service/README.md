# 华为云紧急语音呼叫 APP

这是一个完整的本地后端 + 前端 + 桌面启动器项目，用来接收华为云设备或云端服务上报的告警，并自动创建一场家属端与设备端之间的实时语音通话。

当前版本实现的是：

- 华为云 webhook 告警接收
- 被监护对象与家属端账号绑定
- 家属端演示页
- 设备端演示页
- WebRTC 实时语音通话房间
- 微信小程序 MVP（登录、告警列表、详情、一键拨号、订阅消息授权入口）
- Android 家属端 App（前台全屏跌倒告警、位置查看、回拨）
- Windows 桌面启动器
- PyInstaller 一键打包 `exe`

## 项目目录

`C:\Users\37943\Documents\Codex\2026-07-04\new-chat-5\work\emergency-call-backend`

## 直接运行后端

```powershell
cd C:\Users\37943\Documents\Codex\2026-07-04\new-chat-5\work\emergency-call-backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```

启动后可访问：

- `http://127.0.0.1:8010/` 管理台
- `http://127.0.0.1:8010/caregiver-demo` 家属端
- `http://127.0.0.1:8010/device-demo` 设备端

## 微信小程序 MVP

仓库里已经附带一个可导入微信开发者工具的小程序工程：

`C:\Users\37943\Documents\Codex\2026-07-04\new-chat-5\work\emergency-call-backend\mini-program`

它当前可用于：

- 登录 / 注册现有后端用户
- 登记当前小程序端为 `wechat_miniprogram`
- 查看待处理告警和历史会话
- 查看告警详情
- 一键拨打回拨电话
- 请求订阅消息授权

建议至少配置这些环境变量：

```env
PUBLIC_BASE_URL=https://your-backend.example.com
EMERGENCY_CALL_NUMBER=120
MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS=["your-template-id"]
```

说明：

- 这个版本已经适合做小程序 `MVP` 验证
- 已包含 `openid` 绑定、AppID/AppSecret 登录、订阅消息发送与模板渲染逻辑
- 微信订阅消息仍然遵循一次性授权语义；如果需要连续发送多次，需要用户在小程序里重复点击授权来累计可发送次数
- 后端现在会按“次数”消费授权，每成功发送 1 条只扣减 1 次

## Android 家属端 App

仓库里已经附带一个原生 Android 项目：

`C:\Users\37943\Documents\Codex\2026-07-04\new-chat-5\work\emergency-call-backend\android-caregiver-app`

它当前可用于：

- 配置现有 FastAPI 后端地址
- 注册 Android 紧急联系人设备
- 轮询待处理告警和历史会话
- App 在前台时弹出全屏跌倒告警层
- 查看地图坐标和回拨电话

说明：

- 这版先打通“Android 原生前台强提醒”链路
- 真正的后台推送、锁屏全屏提醒、FCM 通知仍需下一阶段继续接入

## 直接运行桌面 APP

```powershell
cd C:\Users\37943\Documents\Codex\2026-07-04\new-chat-5\work\emergency-call-backend
.venv\Scripts\python.exe desktop_app.py
```

说明：

- 它会自动启动本地服务
- 自动创建独立本地数据目录
- 自动写入一个默认演示对象 `147852369`
- 默认家属账号是 `qiu_father_001`

如果你只想验证启动器是否正常：

```powershell
.venv\Scripts\python.exe desktop_app.py --smoke-test
```

## 一键打包成 exe

```powershell
cd C:\Users\37943\Documents\Codex\2026-07-04\new-chat-5\work\emergency-call-backend
.\build-app.ps1
```

打包完成后输出位置：

- `dist\EmergencyVoiceApp.exe`
- `dist\APP_USAGE.txt`

你也可以直接测试打包好的程序：

```powershell
.\dist\EmergencyVoiceApp.exe --smoke-test
```

## 实际测试流程

1. 打开管理台，确认对象 `147852369` 已存在。
2. 在管理台发送一条测试告警。
3. 打开家属端，用 `qiu_father_001` 注册当前页面。
4. 打开设备端，填写一个设备显示名称。
5. 家属端点击“接听并进入语音”。
6. 设备端点击“加入设备端语音”。
7. 两边浏览器或桌面窗口允许麦克风权限后，即可真实通话。

## 华为云接入格式

可直接向下面地址发送 POST：

```text
POST /webhooks/huawei/{INGEST_KEY}
```

示例：

```json
{
  "subject": "SOS button pressed",
  "message": {
    "severity": "critical",
    "content": "Patient fall detected in room 302.",
    "external_key": "147852369"
  }
}
```

常用字段：

- `severity`: `critical` / `warning` / `info`
- `content`: 告警内容
- `external_key`: 被监护对象或设备编号

## 华为云 IoTDA 跌倒检测

当前后端已经支持从华为云 IoTDA 属性上报中读取 `FALL` 服务的 `accel`、`state`、`fall_count`。

触发规则：

- 先出现连续不少于 `60ms` 的 `accel < 0.45g`
- 随后 `1s` 内出现 `accel > 2.5g`
- 满足后自动生成 `critical` 告警，并复用现有小程序订阅通知和语音会话分发

华为云数据转发建议：

- 转发地址：`https://你的公网后端/webhooks/huawei/{INGEST_KEY}`
- 请求方式：`POST`
- 请求体：转发 IoTDA 设备属性上报原始 JSON 即可
- 设备编号来源：优先使用上报里的 `device_id`；例如截图里的设备 `helmet`，就需要在本系统里创建对象编号 `helmet`

示例属性上报：

```json
{
  "resource": "device.property",
  "event": "report",
  "notify_data": {
    "header": {
      "device_id": "helmet"
    },
    "body": {
      "services": [
        {
          "service_id": "FALL",
          "event_time": "2026-07-06T09:14:17.070Z",
          "properties": {
            "accel": "0.38",
            "state": "NORMAL",
            "fall_count": 2
          }
        }
      ]
    }
  }
}
```

阈值可在 `.env` 中调整：

```env
FALL_FREEFALL_THRESHOLD_G=0.45
FALL_FREEFALL_MIN_MS=60
FALL_IMPACT_THRESHOLD_G=2.5
FALL_IMPACT_WINDOW_MS=1000
```

注意：普通属性刷新不会直接通知小程序，只有满足“失重 + 冲击”组合条件才会触发告警。要让小程序收到通知，还需要先把小程序账号绑定到对应对象编号，并累计足够的订阅通知可发次数。

## 配置项

`.env.example` 中可配置：

```env
INGEST_KEY=replace-with-a-long-random-secret
CALL_PROVIDER=mock
PUBLIC_BASE_URL=http://127.0.0.1:8010
DATABASE_PATH=./data/emergency_call.db
EMERGENCY_CALL_NUMBER=120
MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS=[]
DEFAULT_CONTACTS=["+8613800000000"]
CONTACTS_BY_SEVERITY={"critical":["+8613800000001","+8613800000002"],"warning":["+8613800000003"],"info":["+8613800000004"]}
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
TWILIO_TURN_ENABLED=false
TWILIO_TURN_TTL=3600
AUTO_CONFIRM_SMN_SUBSCRIPTION=true
WEBRTC_ICE_SERVERS=[{"urls":["stun:stun.l.google.com:19302","stun:stun1.l.google.com:19302"]}]
FALL_FREEFALL_THRESHOLD_G=0.45
FALL_FREEFALL_MIN_MS=60
FALL_IMPACT_THRESHOLD_G=2.5
FALL_IMPACT_WINDOW_MS=1000
```

注意：

- 当前这套重点是 APP 内实时语音
- 不是直接拨打运营商电话
- 若以后要接入真实电话外呼，需要单独接语音平台或运营商线路

如果你要做跨网 WebRTC 测试，推荐这样配置：

- 把 `PUBLIC_BASE_URL` 改成你的公网 HTTPS 地址，例如 Cloudflare Tunnel 分配的 `https://xxxx.trycloudflare.com`
- 填入 `TWILIO_ACCOUNT_SID` 和 `TWILIO_AUTH_TOKEN`
- 把 `TWILIO_TURN_ENABLED=true`
- 保留 `WEBRTC_ICE_SERVERS` 作为兜底静态配置

开启后，后端的 `/api/webrtc-config` 会在前端加载房间页面时动态向 Twilio 申请临时 TURN 凭证；如果申请失败，会自动回退到 `WEBRTC_ICE_SERVERS` 中的静态 STUN/TURN 配置。

## 自动测试

```powershell
.venv\Scripts\python.exe -m pytest -q
```

当前测试覆盖：

- 华为云消息解析
- 对象与家属绑定
- 测试告警创建
- 家属端注册
- 通话状态更新
- WebRTC 信令接口
