package cc.duyuan.hkvpn

import android.content.Context
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.widget.*
import androidx.activity.ComponentActivity
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
    private val tunnel = object : Tunnel {
        override fun getName() = "Hong Kong"
        override fun onStateChange(newState: Tunnel.State) { runOnUiThread { renderState(newState) } }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        backend = GoBackend(applicationContext)
        setContentView(buildUi())
        renderState(backend.getState(tunnel))
    }

    private fun buildUi(): View {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(42, 54, 42, 42); gravity = Gravity.CENTER_HORIZONTAL }
        root.addView(TextView(this).apply { text = "HK VPN"; textSize = 30f })
        root.addView(TextView(this).apply { text = "个人香港网络 · WireGuard 主连接"; textSize = 15f })
        endpoint = EditText(this).apply { hint = "控制台地址，例如 https://panel.example.com"; setText(prefs.getString("endpoint", "")); inputType = 33 }
        password = EditText(this).apply { hint = "管理密码（首次获取配置时使用）"; inputType = 129 }
        status = TextView(this).apply { textSize = 16f; setPadding(0, 30, 0, 22) }
        connect = Button(this).apply { text = "Connect"; setOnClickListener { toggle() } }
        listOf(endpoint, password, status, connect).forEach { root.addView(it, LinearLayout.LayoutParams(-1, -2).apply { topMargin = 16 }) }
        root.addView(Button(this).apply { text = "打开系统 VPN 备用说明"; setOnClickListener { showIkev2() } }, LinearLayout.LayoutParams(-1, -2).apply { topMargin = 12 })
        return ScrollView(this).apply { addView(root) }
    }

    private fun toggle() {
        lifecycleScope.launch {
            connect.isEnabled = false
            try {
                if (backend.getState(tunnel) == Tunnel.State.UP) {
                    withContext(Dispatchers.IO) { backend.setState(tunnel, Tunnel.State.DOWN, null) }
                } else {
                    val configText = prefs.getString("config", null) ?: enroll()
                    val config = Config.parse(BufferedReader(StringReader(configText)))
                    withContext(Dispatchers.IO) { backend.setState(tunnel, Tunnel.State.UP, config) }
                }
            } catch (error: Exception) { toast(error.message ?: "连接失败") } finally { connect.isEnabled = true }
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
