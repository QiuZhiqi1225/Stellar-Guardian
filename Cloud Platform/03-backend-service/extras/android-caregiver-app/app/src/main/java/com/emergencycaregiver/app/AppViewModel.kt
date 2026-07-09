package com.emergencycaregiver.app

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class AppUiState(
    val backendUrl: String = "",
    val appUserId: String = "",
    val recipientName: String = "",
    val externalKey: String = "",
    val deviceToken: String = "",
    val feedback: String = "",
    val emergencyCallNumber: String = "",
    val pendingSessions: List<CallSession> = emptyList(),
    val historySessions: List<CallSession> = emptyList(),
    val activeAlert: CallSession? = null,
    val selectedSession: CallSession? = null,
    val isLoading: Boolean = false,
    val isRegistering: Boolean = false,
    val isConfigured: Boolean = false,
)

class AppViewModel(
    private val settingsStore: SettingsStore,
) : ViewModel() {
    private val _state = MutableStateFlow(AppUiState())
    val state: StateFlow<AppUiState> = _state

    private var api: BackendApi? = null
    private var primedPendingIds = emptySet<String>()
    private var hasPrimedPending = false

    init {
        val settings = settingsStore.load()
        api = settings.backendUrl.takeIf { it.isNotBlank() }?.let(BackendApiFactory::create)
        _state.value = AppUiState(
            backendUrl = settings.backendUrl,
            appUserId = settings.appUserId,
            recipientName = settings.recipientName,
            externalKey = settings.externalKey,
            deviceToken = settings.deviceToken,
            isConfigured = settings.backendUrl.isNotBlank() &&
                settings.appUserId.isNotBlank() &&
                settings.recipientName.isNotBlank(),
            feedback = "App started. Fill the fields and tap Save and Register.",
        )
        Log.d(TAG, "init configured=${_state.value.isConfigured} appUserId=${settings.appUserId}")
    }

    fun updateBackendUrl(value: String) {
        _state.update { it.copy(backendUrl = value) }
    }

    fun updateAppUserId(value: String) {
        _state.update { it.copy(appUserId = value) }
    }

    fun updateRecipientName(value: String) {
        _state.update { it.copy(recipientName = value) }
    }

    fun updateExternalKey(value: String) {
        _state.update { it.copy(externalKey = value) }
    }

    fun saveAndRegister() {
        val backendUrl = _state.value.backendUrl.trim()
        val appUserId = _state.value.appUserId.trim()
        val recipientName = _state.value.recipientName.trim()
        val externalKey = _state.value.externalKey.trim()
        val deviceToken = _state.value.deviceToken

        if (backendUrl.isBlank() || appUserId.isBlank() || recipientName.isBlank()) {
            _state.update {
                it.copy(feedback = "Please fill Backend URL, App User ID, and Recipient Name first.")
            }
            return
        }

        val normalizedUrl = BackendApiFactory.normalizeBaseUrl(backendUrl)
        api = BackendApiFactory.create(normalizedUrl)

        val nextSettings = AppSettings(
            backendUrl = normalizedUrl.removeSuffix("/"),
            appUserId = appUserId,
            recipientName = recipientName,
            externalKey = externalKey,
            deviceToken = deviceToken,
        )
        settingsStore.save(nextSettings)

        _state.update {
            it.copy(
                backendUrl = nextSettings.backendUrl,
                appUserId = appUserId,
                recipientName = recipientName,
                externalKey = externalKey,
                isConfigured = true,
                isRegistering = true,
                feedback = "Registering device...",
            )
        }
        Log.d(TAG, "saveAndRegister start backend=${nextSettings.backendUrl} appUserId=$appUserId")

        viewModelScope.launch {
            try {
                val response = withContext(Dispatchers.IO) {
                    requireApi().registerDevice(
                        DeviceRegistrationRequest(
                            appUserId = appUserId,
                            recipientName = recipientName,
                            deviceToken = deviceToken,
                            externalKey = externalKey.ifBlank { null },
                        ),
                    )
                }
                hasPrimedPending = false
                primedPendingIds = emptySet()
                _state.update {
                    it.copy(
                        feedback = "Register success. Linked profiles: ${response.registration?.linkedProfiles ?: 0}. Tap Refresh next.",
                        isRegistering = false,
                    )
                }
                Log.d(TAG, "saveAndRegister success linked=${response.registration?.linkedProfiles ?: 0}")
            } catch (error: Exception) {
                Log.e(TAG, "saveAndRegister failed", error)
                _state.update {
                    it.copy(
                        feedback = "Register failed: ${error.message ?: "Unknown error"}",
                        isRegistering = false,
                    )
                }
            }
        }
    }

    fun refreshAll() {
        val current = _state.value
        if (!current.isConfigured || current.appUserId.isBlank() || current.isLoading) {
            return
        }

        Log.d(TAG, "refreshAll start appUserId=${current.appUserId}")
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, feedback = "Refreshing...") }
            try {
                val result = withContext(Dispatchers.IO) {
                    val apiInstance = requireApi()
                    RefreshBundle(
                        config = apiInstance.getAppConfig(),
                        pending = apiInstance.getPendingSessions(current.appUserId.trim()).items,
                        history = apiInstance.getSessionHistory(current.appUserId.trim()).items,
                    )
                }

                val pending = sanitizeSessions(result.pending)
                val history = sanitizeSessions(result.history)
                val newPendingIds = pending.map { it.sessionId }.toSet()
                val newSession = if (hasPrimedPending) {
                    pending.firstOrNull { !primedPendingIds.contains(it.sessionId) }
                } else {
                    null
                }
                primedPendingIds = newPendingIds
                hasPrimedPending = true

                _state.update {
                    it.copy(
                        emergencyCallNumber = result.config.emergencyCallNumber,
                        pendingSessions = pending,
                        historySessions = history,
                        activeAlert = newSession ?: it.activeAlert,
                        selectedSession = syncSelectedSession(
                            sessionId = it.selectedSession?.sessionId,
                            pending = pending,
                            history = history,
                            fallback = it.selectedSession,
                        ),
                        isLoading = false,
                        feedback = "Refresh success.",
                    )
                }
                Log.d(TAG, "refreshAll success pending=${pending.size} history=${history.size}")
            } catch (error: Exception) {
                Log.e(TAG, "refreshAll failed", error)
                _state.update {
                    it.copy(
                        isLoading = false,
                        feedback = "Refresh failed: ${error.message ?: "Unknown error"}",
                    )
                }
            }
        }
    }

    private fun sanitizeSessions(items: List<CallSession>): List<CallSession> {
        return items
            .distinctBy { it.sessionId }
            .take(10)
    }

    private fun syncSelectedSession(
        sessionId: String?,
        pending: List<CallSession>,
        history: List<CallSession>,
        fallback: CallSession?,
    ): CallSession? {
        if (sessionId.isNullOrBlank()) return fallback
        return (pending + history).firstOrNull { it.sessionId == sessionId } ?: fallback
    }

    fun openSessionDetail(sessionId: String) {
        viewModelScope.launch {
            try {
                val detail = withContext(Dispatchers.IO) {
                    requireApi().getSessionDetail(sessionId).item
                }
                _state.update {
                    it.copy(
                        selectedSession = detail ?: it.selectedSession,
                        feedback = "Loaded session detail.",
                    )
                }
            } catch (error: Exception) {
                Log.e(TAG, "openSessionDetail failed", error)
                _state.update {
                    it.copy(feedback = "Load detail failed: ${error.message ?: "Unknown error"}")
                }
            }
        }
    }

    fun closeSessionDetail() {
        _state.update { it.copy(selectedSession = null) }
    }

    fun dismissAlert() {
        _state.update { it.copy(activeAlert = null) }
    }

    fun updateSessionStatus(sessionId: String, status: String) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    requireApi().updateSessionStatus(sessionId, SessionStatusUpdateRequest(status))
                }
                if (_state.value.activeAlert?.sessionId == sessionId) {
                    dismissAlert()
                }
                _state.update { it.copy(feedback = "Status updated to $status.") }
                refreshAll()
            } catch (error: Exception) {
                Log.e(TAG, "updateSessionStatus failed", error)
                _state.update {
                    it.copy(feedback = "Status update failed: ${error.message ?: "Unknown error"}")
                }
            }
        }
    }

    private fun requireApi(): BackendApi {
        return api ?: throw IllegalStateException("Please save the backend URL and register this device first.")
    }

    private data class RefreshBundle(
        val config: AppConfigResponse,
        val pending: List<CallSession>,
        val history: List<CallSession>,
    )

    class Factory(
        private val settingsStore: SettingsStore,
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return AppViewModel(settingsStore) as T
        }
    }

    companion object {
        private const val TAG = "EmergencyCaregiver"
    }
}
