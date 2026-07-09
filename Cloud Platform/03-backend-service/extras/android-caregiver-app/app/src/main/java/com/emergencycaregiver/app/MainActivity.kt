package com.emergencycaregiver.app

import android.os.Bundle
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {
    private val viewModel by viewModels<AppViewModel> {
        AppViewModel.Factory(SettingsStore(applicationContext))
    }

    private lateinit var backendUrlInput: EditText
    private lateinit var appUserIdInput: EditText
    private lateinit var recipientNameInput: EditText
    private lateinit var externalKeyInput: EditText
    private lateinit var deviceTokenText: TextView
    private lateinit var feedbackText: TextView
    private lateinit var countsText: TextView
    private lateinit var pendingPreviewText: TextView
    private lateinit var historyPreviewText: TextView
    private lateinit var saveButton: Button
    private lateinit var refreshButton: Button
    private lateinit var progressBar: ProgressBar

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.d(TAG, "onCreate")
        title = "Emergency Caregiver"
        setContentView(buildContentView())
        bindActions()
        observeState()
    }

    override fun onStart() {
        super.onStart()
        Log.d(TAG, "onStart")
    }

    override fun onResume() {
        super.onResume()
        Log.d(TAG, "onResume")
    }

    private fun buildContentView(): View {
        val scrollView = ScrollView(this)
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(24))
        }
        scrollView.addView(
            container,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ),
        )

        container.addView(makeTitle("Emergency Caregiver"))
        container.addView(makeHint("Use this stable page first. After registration succeeds, tap Refresh manually."))

        backendUrlInput = makeInput(
            hint = "Backend URL, e.g. http://113.54.198.222:8000",
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI,
        )
        appUserIdInput = makeInput(
            hint = "App User ID, e.g. bajixiang_android",
            inputType = InputType.TYPE_CLASS_TEXT,
        )
        recipientNameInput = makeInput(
            hint = "Recipient Name, e.g. Bajixiang Android",
            inputType = InputType.TYPE_CLASS_TEXT,
        )
        externalKeyInput = makeInput(
            hint = "External Key, e.g. 6a4b66c17f2e6c302f827a87_qzqwytbjx",
            inputType = InputType.TYPE_CLASS_TEXT,
        )

        container.addView(makeLabel("Backend URL"))
        container.addView(backendUrlInput)
        container.addView(makeLabel("App User ID"))
        container.addView(appUserIdInput)
        container.addView(makeLabel("Recipient Name"))
        container.addView(recipientNameInput)
        container.addView(makeLabel("External Key"))
        container.addView(externalKeyInput)

        deviceTokenText = makeValueText()
        container.addView(makeLabel("Device Token"))
        container.addView(deviceTokenText)

        progressBar = ProgressBar(this).apply {
            isIndeterminate = true
            isVisible = false
        }
        container.addView(progressBar)

        saveButton = Button(this).apply {
            text = "Save and Register"
        }
        refreshButton = Button(this).apply {
            text = "Refresh"
        }
        container.addView(saveButton, matchWidthParams(topMarginDp = 12))
        container.addView(refreshButton, matchWidthParams(topMarginDp = 8))

        feedbackText = makeValueText().apply {
            setTextColor(0xFF8B1E1E.toInt())
        }
        countsText = makeValueText()
        pendingPreviewText = makeMultilineBlock()
        historyPreviewText = makeMultilineBlock()

        container.addView(makeLabel("Status"))
        container.addView(feedbackText)
        container.addView(makeLabel("Counts"))
        container.addView(countsText)
        container.addView(makeLabel("Pending Alerts"))
        container.addView(pendingPreviewText)
        container.addView(makeLabel("History"))
        container.addView(historyPreviewText)

        return scrollView
    }

    private fun bindActions() {
        saveButton.setOnClickListener {
            syncInputsToViewModel()
            viewModel.saveAndRegister()
        }
        refreshButton.setOnClickListener {
            syncInputsToViewModel()
            viewModel.refreshAll()
        }
        listOf(backendUrlInput, appUserIdInput, recipientNameInput, externalKeyInput).forEach { input ->
            input.addTextChangedListener(SimpleTextWatcher { syncInputsToViewModel() })
        }
    }

    private fun observeState() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                viewModel.state.collect { state ->
                    Log.d(
                        TAG,
                        "render configured=${state.isConfigured} loading=${state.isLoading} " +
                            "registering=${state.isRegistering} pending=${state.pendingSessions.size} " +
                            "history=${state.historySessions.size}",
                    )
                    render(state)
                }
            }
        }
    }

    private fun render(state: AppUiState) {
        updateTextIfNeeded(backendUrlInput, state.backendUrl)
        updateTextIfNeeded(appUserIdInput, state.appUserId)
        updateTextIfNeeded(recipientNameInput, state.recipientName)
        updateTextIfNeeded(externalKeyInput, state.externalKey)

        deviceTokenText.text = state.deviceToken.ifBlank { "-" }
        feedbackText.text = state.feedback.ifBlank { "Ready." }
        countsText.text = buildString {
            append("Pending: ")
            append(state.pendingSessions.size)
            append(" | History: ")
            append(state.historySessions.size)
            if (state.emergencyCallNumber.isNotBlank()) {
                append(" | Call: ")
                append(state.emergencyCallNumber)
            }
        }
        pendingPreviewText.text = formatSessions(state.pendingSessions)
        historyPreviewText.text = formatSessions(state.historySessions)

        progressBar.isVisible = state.isLoading || state.isRegistering
        saveButton.isEnabled = !state.isRegistering
        refreshButton.isEnabled = state.isConfigured && !state.isLoading
    }

    private fun formatSessions(items: List<CallSession>): String {
        if (items.isEmpty()) {
            return "No data yet."
        }
        return items.take(5).joinToString("\n\n") { session ->
            buildString {
                append("Session: ")
                append(session.sessionId.ifBlank { "-" })
                append("\nStatus: ")
                append(session.status.ifBlank { "-" })
                append("\nTitle: ")
                append(session.eventTitle.ifBlank { session.recipientName.ifBlank { "-" } })
                append("\nBody: ")
                append(session.eventBody.ifBlank { session.detail.ifBlank { "-" } })
                if (session.targetExternalKey.isNotBlank()) {
                    append("\nTarget: ")
                    append(session.targetExternalKey)
                }
            }
        }
    }

    private fun syncInputsToViewModel() {
        viewModel.updateBackendUrl(backendUrlInput.text.toString())
        viewModel.updateAppUserId(appUserIdInput.text.toString())
        viewModel.updateRecipientName(recipientNameInput.text.toString())
        viewModel.updateExternalKey(externalKeyInput.text.toString())
    }

    private fun updateTextIfNeeded(editText: EditText, value: String) {
        if (editText.isFocused) return
        val current = editText.text?.toString().orEmpty()
        if (current != value) {
            editText.setText(value)
            editText.setSelection(editText.text?.length ?: 0)
        }
    }

    private fun makeTitle(text: String): TextView {
        return TextView(this).apply {
            this.text = text
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 24f)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            gravity = Gravity.START
        }.also {
            it.layoutParams = matchWidthParams(bottomMarginDp = 8)
        }
    }

    private fun makeHint(text: String): TextView {
        return TextView(this).apply {
            this.text = text
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setTextColor(0xFF5F6B76.toInt())
        }.also {
            it.layoutParams = matchWidthParams(bottomMarginDp = 16)
        }
    }

    private fun makeLabel(text: String): TextView {
        return TextView(this).apply {
            this.text = text
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }.also {
            it.layoutParams = matchWidthParams(topMarginDp = 12, bottomMarginDp = 6)
        }
    }

    private fun makeInput(hint: String, inputType: Int): EditText {
        return EditText(this).apply {
            this.hint = hint
            this.inputType = inputType
            setSingleLine()
            setPadding(dp(12), dp(12), dp(12), dp(12))
        }.also {
            it.layoutParams = matchWidthParams()
        }
    }

    private fun makeValueText(): TextView {
        return TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setTextColor(0xFF1F2933.toInt())
        }.also {
            it.layoutParams = matchWidthParams()
        }
    }

    private fun makeMultilineBlock(): TextView {
        return TextView(this).apply {
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setTextColor(0xFF1F2933.toInt())
            setLineSpacing(0f, 1.15f)
            setPadding(dp(12), dp(12), dp(12), dp(12))
            setBackgroundColor(0xFFF1F5F9.toInt())
        }.also {
            it.layoutParams = matchWidthParams(bottomMarginDp = 4)
        }
    }

    private fun matchWidthParams(topMarginDp: Int = 0, bottomMarginDp: Int = 0): LinearLayout.LayoutParams {
        return LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply {
            topMargin = dp(topMarginDp)
            bottomMargin = dp(bottomMarginDp)
        }
    }

    private fun dp(value: Int): Int {
        return TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP,
            value.toFloat(),
            resources.displayMetrics,
        ).toInt()
    }

    private class SimpleTextWatcher(
        private val afterChanged: () -> Unit,
    ) : TextWatcher {
        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit

        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit

        override fun afterTextChanged(s: Editable?) {
            afterChanged()
        }
    }

    companion object {
        private const val TAG = "EmergencyCaregiver"
    }
}
