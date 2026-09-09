package cc.duyuan.hkvpn

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object ApiClient {
    fun enroll(baseUrl: String, password: String, deviceId: String, platform: String): String {
        require(password.isNotBlank()) { "请输入管理密码" }
        val body = JSONObject(mapOf("password" to password, "clientId" to deviceId, "platform" to platform)).toString().toByteArray()
        val connection = URL("$baseUrl/api/client/enroll").openConnection() as HttpURLConnection
        connection.requestMethod = "POST"; connection.doOutput = true; connection.connectTimeout = 15_000; connection.readTimeout = 20_000
        connection.setRequestProperty("Content-Type", "application/json")
        connection.outputStream.use { it.write(body) }
        val response = (if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream).bufferedReader().readText()
        if (connection.responseCode !in 200..299) throw IllegalStateException(JSONObject(response).optString("error", "服务器拒绝请求"))
        return JSONObject(response).getString("wireGuardConfig")
    }
}
