package app.northernlights.tv

import android.os.Bundle
import android.view.KeyEvent
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import kotlin.concurrent.thread

/** First-run / change-server screen. Validates, tests GET /healthz, then saves. */
class SettingsActivity : AppCompatActivity() {
    private lateinit var address: EditText
    private lateinit var status: TextView
    private lateinit var save: Button
    private lateinit var serverVersion: TextView
    private lateinit var appVersion: TextView
    private lateinit var checkUpdate: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        address = findViewById(R.id.address)
        status = findViewById(R.id.status)
        save = findViewById(R.id.save)
        serverVersion = findViewById(R.id.server_version)
        appVersion = findViewById(R.id.app_version)
        checkUpdate = findViewById(R.id.check_update)
        appVersion.text = getString(R.string.app_version, "1.0.${AppUpdater.installedBuild(this)}")
        checkUpdate.setOnClickListener { checkForUpdate() }

        val existing = ServerConfig.load(this)
        address.setText(existing ?: ServerConfig.DEFAULT_PREFILL)
        address.setSelection(address.text.length)
        if (existing != null) {
            thread(name = "nl-version") {
                val version = ServerConfig.fetchVersion(existing)
                runOnUiThread {
                    if (!isFinishing && !isDestroyed && version != null) {
                        serverVersion.text = getString(R.string.server_version, version)
                    }
                }
            }
        }

        save.setOnClickListener { trySave() }
        address.setOnEditorActionListener { _, actionId, event ->
            val enter = event?.keyCode == KeyEvent.KEYCODE_ENTER && event.action == KeyEvent.ACTION_DOWN
            if (actionId == EditorInfo.IME_ACTION_DONE || enter) {
                save.requestFocus()
                true
            } else {
                false
            }
        }
        address.requestFocus()
    }

    override fun onResume() {
        super.onResume()
        AppUpdater.resumeInstallIfAllowed(this)
    }

    private fun checkForUpdate() {
        checkUpdate.isEnabled = false
        showStatus(getString(R.string.update_checking), null)
        thread(name = "nl-update-check") {
            val build = AppUpdater.findNewerBuild(applicationContext)
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                checkUpdate.isEnabled = true
                if (build == null) {
                    showStatus(getString(R.string.update_none), true)
                } else {
                    showStatus("", null)
                    AppUpdater.offer(this, build)
                }
            }
        }
    }

    private fun showStatus(text: String, ok: Boolean?) {
        status.text = text
        val color = when (ok) {
            true -> R.color.aurora_green
            false -> R.color.aurora_violet
            null -> R.color.text_secondary
        }
        status.setTextColor(ContextCompat.getColor(this, color))
    }

    private fun trySave() {
        val normalized = ServerConfig.normalize(address.text.toString())
        if (normalized == null) {
            showStatus("Enter an address like http://192.168.1.20:8000", false)
            address.requestFocus()
            return
        }
        address.setText(normalized)
        save.isEnabled = false
        showStatus(getString(R.string.testing), null)
        thread(name = "nl-healthz") {
            val error = ServerConfig.testConnection(normalized)
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                save.isEnabled = true
                if (error == null) {
                    ServerConfig.save(this, normalized)
                    showStatus(getString(R.string.connected), true)
                    status.postDelayed({
                        if (!isFinishing) {
                            setResult(RESULT_OK)
                            finish()
                        }
                    }, 600)
                } else {
                    showStatus(error, false)
                    save.requestFocus()
                }
            }
        }
    }
}
