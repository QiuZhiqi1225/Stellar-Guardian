# Android 紧急联系人 App

这是给家属或紧急联系人使用的 Android 原生看护端第一版，直接复用当前 FastAPI 后端。

当前已实现：

- 手动配置后端地址
- 填写并保存 `app_user_id`
- 首次注册 Android 设备到后端
- 轮询待处理告警和历史会话
- App 在前台时弹出全屏跌倒告警页
- 查看告警详情、位置、回拨电话
- 一键打开地图或复制坐标

当前还未实现：

- FCM 推送
- 后台全屏强提醒
- 锁屏 `full-screen intent`
- 原生语音通话或 WebRTC

## 打开方式

1. 用 Android Studio 打开 [android-caregiver-app](C:/Users/37943/Documents/Codex/2026-07-04/new-chat-5/work/emergency-call-backend/android-caregiver-app)
2. 等待 Gradle Sync 完成
3. 运行 `app` 模块到 Android 手机或模拟器

## 首次配置

1. 填后端地址
   例子：
   - 真机连你电脑本地后端：`http://你的电脑局域网IP:8000`
   - Android 模拟器连本机：`http://10.0.2.2:8000`
   - 公网部署地址：`https://你的域名`
2. 填紧急联系人 `app_user_id`
3. 填显示名称
4. 如果这是第一次绑定某个受护对象，填 `external_key`
5. 点击“保存并注册 Android 设备”

## 当前提醒机制

- App 在前台：收到新的待处理会话时，直接弹全屏告警层并震动响铃
- App 不在前台：这版暂时不会自动弹后台全屏提醒，后续接 FCM 后再补

## 和现有后端的接口

- `GET /api/mobile/app-config`
- `POST /api/mobile/register-device`
- `GET /api/app-users/{app_user_id}/pending-sessions`
- `GET /api/app-users/{app_user_id}/sessions`
- `GET /api/call-sessions/{session_id}`
- `POST /api/call-sessions/{session_id}/status`
