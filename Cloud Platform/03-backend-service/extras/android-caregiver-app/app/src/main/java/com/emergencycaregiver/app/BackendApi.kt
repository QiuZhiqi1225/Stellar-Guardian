package com.emergencycaregiver.app

import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

interface BackendApi {
    @GET("/api/mobile/app-config")
    suspend fun getAppConfig(): AppConfigResponse

    @POST("/api/mobile/register-device")
    suspend fun registerDevice(@Body request: DeviceRegistrationRequest): RegistrationResponse

    @GET("/api/app-users/{appUserId}/pending-sessions")
    suspend fun getPendingSessions(@Path("appUserId") appUserId: String): SessionListResponse

    @GET("/api/app-users/{appUserId}/sessions")
    suspend fun getSessionHistory(@Path("appUserId") appUserId: String): SessionListResponse

    @GET("/api/call-sessions/{sessionId}")
    suspend fun getSessionDetail(@Path("sessionId") sessionId: String): SessionDetailResponse

    @POST("/api/call-sessions/{sessionId}/status")
    suspend fun updateSessionStatus(
        @Path("sessionId") sessionId: String,
        @Body request: SessionStatusUpdateRequest,
    ): SessionStatusUpdateResponse
}

object BackendApiFactory {
    fun create(baseUrl: String): BackendApi {
        val normalized = normalizeBaseUrl(baseUrl)
        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }
        val client = OkHttpClient.Builder()
            .addInterceptor(logging)
            .build()

        return Retrofit.Builder()
            .baseUrl(normalized)
            .addConverterFactory(GsonConverterFactory.create())
            .client(client)
            .build()
            .create(BackendApi::class.java)
    }

    fun normalizeBaseUrl(input: String): String {
        val raw = input.trim()
        if (raw.isEmpty()) {
            return ""
        }
        val withProtocol = if (raw.startsWith("http://") || raw.startsWith("https://")) {
            raw
        } else {
            "http://$raw"
        }
        return if (withProtocol.endsWith("/")) withProtocol else "$withProtocol/"
    }
}
