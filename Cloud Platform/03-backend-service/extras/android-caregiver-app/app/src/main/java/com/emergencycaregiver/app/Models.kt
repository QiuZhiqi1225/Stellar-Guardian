package com.emergencycaregiver.app

import com.google.gson.annotations.SerializedName

data class AppConfigResponse(
    @SerializedName("public_base_url") val publicBaseUrl: String = "",
    @SerializedName("emergency_call_number") val emergencyCallNumber: String = "",
)

data class SessionListResponse(
    val items: List<CallSession> = emptyList(),
)

data class SessionDetailResponse(
    val item: CallSession? = null,
)

data class RegistrationResponse(
    val status: String = "",
    val registration: DeviceRegistration? = null,
)

data class DeviceRegistration(
    @SerializedName("app_user_id") val appUserId: String = "",
    @SerializedName("device_token") val deviceToken: String = "",
    val platform: String = "",
    @SerializedName("recipient_name") val recipientName: String = "",
    @SerializedName("linked_profiles") val linkedProfiles: Int = 0,
)

data class SessionStatusUpdateRequest(
    val status: String,
)

data class SessionStatusUpdateResponse(
    val status: String = "",
    val session: CallSession? = null,
)

data class DeviceRegistrationRequest(
    @SerializedName("app_user_id") val appUserId: String,
    @SerializedName("recipient_name") val recipientName: String,
    @SerializedName("device_token") val deviceToken: String,
    val platform: String = "android",
    @SerializedName("external_key") val externalKey: String? = null,
)

data class CallSession(
    @SerializedName("session_id") val sessionId: String = "",
    @SerializedName("event_id") val eventId: String = "",
    @SerializedName("recipient_name") val recipientName: String = "",
    @SerializedName("app_user_id") val appUserId: String = "",
    val status: String = "",
    val detail: String = "",
    @SerializedName("event_title") val eventTitle: String = "",
    @SerializedName("event_body") val eventBody: String = "",
    @SerializedName("event_occurred_at") val eventOccurredAt: String = "",
    @SerializedName("event_severity") val eventSeverity: String = "",
    @SerializedName("target_external_key") val targetExternalKey: String = "",
    @SerializedName("profile_display_name") val profileDisplayName: String = "",
    @SerializedName("callback_phone") val callbackPhone: String = "",
    val location: AlertLocation? = null,
)

data class AlertLocation(
    val latitude: Double = 0.0,
    val longitude: Double = 0.0,
    val label: String = "",
)

data class AppSettings(
    val backendUrl: String = "",
    val appUserId: String = "",
    val recipientName: String = "",
    val externalKey: String = "",
    val deviceToken: String = "",
)
