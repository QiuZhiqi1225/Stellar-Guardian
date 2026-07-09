# Mobile App Plan

## Current State

- Backend: FastAPI + SQLite
- Frontend: static HTML/CSS/JS pages under `app/static`
- Desktop packaging: `pywebview` + `PyInstaller`
- Current alert mode:
  - browser/desktop in-app alert popup
  - desktop background alert agent

## Best Path To Phone App

The current project is already close to a mobile web app, because the UI is plain HTML/JS.
The fastest path is:

1. Keep the existing FastAPI backend.
2. Reuse the current pages as the mobile UI.
3. Package the frontend as a mobile shell with Capacitor.
4. Add native push notifications for Android/iPhone.

## Recommended Stack

- UI shell: Capacitor
- Android push:
  - FCM for common Android phones
  - Huawei Push Kit for Huawei phones without Google services
- iPhone push:
  - APNs
- Real-time voice:
  - keep current WebRTC room page
  - later add TURN server for cross-network reliability

## Why Capacitor

- It can directly host the current HTML/JS pages.
- Migration cost is much lower than rewriting everything in Flutter or React Native.
- It supports native plugins for push, permissions, background wakeup, and app launch from notifications.

## What Needs To Change

### Phase 1: Mobile Shell

- Create a `mobile-app/` folder
- Add Capacitor Android project
- Load current web pages inside the app
- Add backend base URL configuration screen

### Phase 2: Real Push

- Generate a real mobile device token on the phone
- Upload that token to `/api/mobile/register-device`
- Backend stores the phone token instead of the current web demo token
- When Huawei cloud alert arrives, backend sends push to the phone

### Phase 3: Tap Notification To Open Session

- Push payload includes:
  - `session_id`
  - `external_key`
  - `alert_title`
  - `alert_body`
- User taps the notification
- App opens:
  - caregiver page, or
  - device page, or
  - call room page directly

### Phase 4: Cross-Network Voice Stability

- Add a TURN server
- Update WebRTC ICE config
- Verify Android microphone permissions and background behavior

## Suggested Delivery Order

1. Android version first
2. Push notifications first
3. Voice room second
4. iPhone version after Android is stable

## Practical Notes

- If you want "like earthquake warning" behavior on phones, native push is required.
- A plain web page or local browser page cannot reliably do this when the app is fully closed.
- Desktop background alert and mobile push are two different mechanisms.

## Next Build Target

The next concrete target should be:

- Build an Android app shell
- Let the user enter:
  - backend URL
  - custom app user ID
  - external key
- Register the real phone token
- Receive a native push notification
- Tap the notification and enter the alert page
