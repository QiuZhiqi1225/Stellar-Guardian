# 微信小程序说明

这个目录可以直接导入微信开发者工具。

## 导入目录

`C:\Users\37943\Documents\Codex\2026-07-04\new-chat-5\work\emergency-call-backend\mini-program`

## 当前页面结构

- `首页`
- `系统配置`
- `注册 / 登录`
- `设备登记`
- `告警列表`
- `告警详情`

## 这一版已经接好的能力

- 设备登记
- `wx.makePhoneCall` 一键拨号
- `wx.login` 绑定微信小程序账号
- `wx.requestSubscribeMessage` 申请订阅通知权限
- 后端告警触发后，尝试发送微信订阅消息

## 后端需要的环境变量

```env
PUBLIC_BASE_URL=https://constructed-rca-don-shots.trycloudflare.com
EMERGENCY_CALL_NUMBER=120

WECHAT_MINI_APP_ID=你的小程序AppID
WECHAT_MINI_APP_SECRET=你的小程序AppSecret

MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS=["你的模板ID"]
WECHAT_MINI_SUBSCRIBE_TEMPLATES=[
  {
    "id": "你的模板ID",
    "page": "pages/detail/detail?sessionId={session_id}",
    "data": {
      "thing1": { "value": "{event_title}" },
      "thing2": { "value": "{event_severity_label}" }
    }
  }
]
```

## 设备页使用顺序

1. 先填 `用户 ID`、`接收人名称`
2. 点 `登记设备`
3. 点 `绑定微信`
4. 点 `申请通知授权`

补充说明：

- 告警是按 `对象编号 external_key` 分发的，不是按用户注册动作自动分发
- 如果后台当前只有默认演示对象 `147852369`，那测试告警当然也只会先打到这个对象
- 你要让自己的账号接收别的对象告警，需要先把这个账号绑定到对应对象编号
- 现在小程序支持两种方式绑定：注册页填写 `关联对象编号`，或设备页填写 `关联对象编号` 后再登记设备

## 订阅通知现在的行为

- 微信小程序订阅消息本质上还是一次性授权
- 现在后端已经支持“重复授权累计次数”
- 也就是你每点一次 `申请通知授权`，就会多 1 次可发送机会
- 后端每成功发送 1 条通知，只会扣减 1 次，不会把同模板的全部授权一次清空
- 如果你希望连续收到很多次告警通知，就在设备页多点几次授权，把次数先攒起来

## 如果通知发不出去

优先检查这 4 项：

1. `WECHAT_MINI_APP_ID` 和 `WECHAT_MINI_APP_SECRET` 是否已填写
2. `MINI_PROGRAM_SUBSCRIBE_TEMPLATE_IDS` 是否有模板 ID
3. `WECHAT_MINI_SUBSCRIBE_TEMPLATES` 里的字段名是否和微信后台模板字段一致
4. 小程序后台是否已经配置合法域名

## 连接华为云 IoTDA 跌倒告警

- 后端会读取 IoTDA 属性上报里的 `FALL.accel`
- 规则是先 `accel < 0.45g` 持续至少 `60ms`，随后 `1s` 内 `accel > 2.5g`
- 命中后会生成 `critical` 告警，再发送小程序订阅通知
- 华为云设备 `device_id` 要和本系统的 `对象编号` 一致；例如截图设备是 `helmet`，管理台对象编号也要创建为 `helmet`
- 小程序账号需要在注册页或设备页填写同一个 `关联对象编号`，并提前申请足够的通知授权次数
