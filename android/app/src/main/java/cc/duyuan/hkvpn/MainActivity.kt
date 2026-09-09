package cc.duyuan.hkvpn

import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.net.VpnService
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.lifecycleScope
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.BufferedReader
import java.io.StringReader
import java.util.UUID

class MainActivity : ComponentActivity() {
    private lateinit var backend: GoBackend
    private lateinit var status: TextView
    private lateinit var connect: Button
    private lateinit var endpoint: EditText
    private lateinit var password: EditText
    private val prefs by lazy { getSharedPreferences("hk_vpn", Context.MODE_PRIVATE) }
    private var pendingConnect = false
    private val requestVpnPermission = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == RESULT_OK && pendingConnect) connectTunnel()
        else if (pendingConnect) toast("未授予系统 VPN 权限，无法连接")
        pendingConnect = false
    }
    private val tunnel = object : Tunnel {
        override fun getName() = "Hong Kong"
        override fun onStateChange(newState: Tunnel.State) { runOnUiThread { renderState(newState) } }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        backend = GoBackend(applicationContext)
        setContentView(buildUi(savedInstanceState?.getString("endpoint")))
        renderState(backend.getState(tunnel))
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("endpoint", endpoint.text.toString())
        super.onSaveInstanceState(outState)
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        // Rebuild with density-independent spacing and preserve the typed endpoint.
        setContentView(buildUi(endpoint.text.toString()))
        renderState(backend.getState(tunnel))
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    private fun wideScreen() = resources.configuration.smallestScreenWidthDp >= 600
    private fun layoutParams(top: Int = 0) = LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(top) }

    private fun buildUi(restoredEndpoint: String?): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(if (wideScreen()) 32 else 20), dp(28), dp(if (wideScreen()) 32 else 20), dp(28))
        }
        root.addView(TextView(this).apply { text = "HK VPN"; textSize = if (wideScreen()) 34f else 30f })
        root.addView(TextView(this).apply { text = "个人香港网络 · WireGuard 主连接"; textSize = 15f }, layoutParams(6))
        endpoint = EditText(this).apply {
            hint = "控制台地址，例如 https://panel.example.com"
            setText(restoredEndpoint ?: prefs.getString("endpoint", ""))
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
            isSingleLine = true
        }
        password = EditText(this).apply {
            hint = "管理密码（首次获取配置时使用）"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
            isSingleLine = true
        }
        status = TextView(this).apply { textSize = 16f; setPadding(0, dp(8), 0, dp(2)) }
        connect = Button(this).apply { text = "Connect"; minHeight = dp(48); setOnClickListener { toggle() } }
        root.addView(endpoint, layoutParams(24)); root.addView(password, layoutParams(12)); root.addView(status, layoutParams(14)); root.addView(connect, layoutParams(10))
        root.addView(Button(this).apply { text = "打开系统 VPN 备用说明"; minHeight = dp(48); setOnClickListener { showIkev2() } }, layoutParams(10))

        val frame = FrameLayout(this)
        frame.addView(root, FrameLayout.LayoutParams(if (wideScreen()) dp(560) else -1, -2).apply { gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL })
        return ScrollView(this).apply { isFillViewport = true; addView(frame, ScrollView.LayoutParams(-1, -2)) }
    }

    private fun toggle() {
        lifecycleScope.launch {
            connect.isEnabled = false
            try {
                if (backend.getState(tunnel) == Tunnel.State.UP) {
                    withContext(Dispatchers.IO) { backend.setState(tunnel, Tunnel.State.DOWN, null) }
                } else requestSystemVpnPermission()
            } catch (error: Exception) { toast(error.message ?: "连接失败") } finally { connect.isEnabled = true }
        }
    }

    private fun requestSystemVpnPermission() {
        pendingConnect = true
        val permissionIntent: Intent? = VpnService.prepare(this)
        if (permissionIntent == null) connectTunnel() else requestVpnPermission.launch(permissionIntent)
    }

    private fun connectTunnel() {
        lifecycleScope.launch {
            connect.isEnabled = false
            try {
                val configText = prefs.getString("config", null) ?: enroll()
                val config = Config.parse(BufferedReader(StringReader(configText)))
                withContext(Dispatchers.IO) { backend.setState(tunnel, Tunnel.State.UP, config) }
            } catch (error: Exception) { toast(error.message ?: "连接失败") }
            finally { connect.isEnabled = true }
        }
    }

    private suspend fun enroll(): String = withContext(Dispatchers.IO) {
        val base = endpoint.text.toString().trim().removeSuffix("/")
        require(base.startsWith("https://")) { "控制台地址必须以 https:// 开头" }
        val deviceId = prefs.getString("device_id", null) ?: UUID.randomUUID().toString().also { prefs.edit().putString("device_id", it).apply() }
        val config = ApiClient.enroll(base, password.text.toString(), deviceId, "Android")
        prefs.edit().putString("endpoint", base).putString("config", config).apply()
        config
    }

    private fun showIkev2() { toast("备用连接：在控制台设备详情中复制 IKEv2 服务器、用户名和密码，并在系统 VPN 设置中添加 IKEv2/IPsec EAP。") }
    private fun renderState(state: Tunnel.State) { val up = state == Tunnel.State.UP; status.text = if (up) "已连接到 Hong Kong" else "未连接"; connect.text = if (up) "Disconnect" else "Connect" }
    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()
}
