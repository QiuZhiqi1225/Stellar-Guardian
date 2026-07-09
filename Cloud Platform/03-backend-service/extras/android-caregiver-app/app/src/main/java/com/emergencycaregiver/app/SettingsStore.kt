package com.emergencycaregiver.app

import android.content.Context
import java.util.UUID

class SettingsStore(context: Context) {
    private val prefs = context.getSharedPreferences("emergency_caregiver_app", Context.MODE_PRIVATE)

    fun load(): AppSettings {
        val savedToken = prefs.getString(KEY_DEVICE_TOKEN, null)?.trim().orEmpty()
        val deviceToken = if (savedToken.isNotEmpty()) {
            savedToken
        } else {
            "android-${UUID.randomUUID()}"
        }
        if (savedToken.isEmpty()) {
            prefs.edit().putString(KEY_DEVICE_TOKEN, deviceToken).apply()
        }
        return AppSettings(
            backendUrl = prefs.getString(KEY_BACKEND_URL, "").orEmpty(),
            appUserId = prefs.getString(KEY_APP_USER_ID, "").orEmpty(),
            recipientName = prefs.getString(KEY_RECIPIENT_NAME, "").orEmpty(),
            externalKey = prefs.getString(KEY_EXTERNAL_KEY, "").orEmpty(),
            deviceToken = deviceToken,
        )
    }

    fun save(settings: AppSettings) {
        prefs.edit()
            .putString(KEY_BACKEND_URL, settings.backendUrl)
            .putString(KEY_APP_USER_ID, settings.appUserId)
            .putString(KEY_RECIPIENT_NAME, settings.recipientName)
            .putString(KEY_EXTERNAL_KEY, settings.externalKey)
            .putString(KEY_DEVICE_TOKEN, settings.deviceToken)
            .apply()
    }

    companion object {
        private const val KEY_BACKEND_URL = "backend_url"
        private const val KEY_APP_USER_ID = "app_user_id"
        private const val KEY_RECIPIENT_NAME = "recipient_name"
        private const val KEY_EXTERNAL_KEY = "external_key"
        private const val KEY_DEVICE_TOKEN = "device_token"
    }
}
