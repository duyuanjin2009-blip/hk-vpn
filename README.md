# HK VPN Suite

个人自用的香港服务器网络套件，包含：

- 浏览器控制台：设备、WireGuard 配置、IKEv2 账号、FLClash 订阅 URL 与撤销管理。
- Android 原生客户端：登录控制台后用 WireGuard 一键连接。
- Windows 原生客户端：登录控制台后调用本机官方 WireGuard 创建一键隧道。
- 系统设置备用：StrongSwan IKEv2/IPsec EAP。
- FLClash 备用：每台设备独立的 Mihomo YAML 长期订阅 URL。

开始部署前先阅读 [宝塔部署方案](docs/BAOTA_DEPLOYMENT.md) 和 [详细配置方案](docs/CONFIGURATION.md)。

## 构建

GitHub Actions 会自动执行服务器单元测试，并产出 Android APK 与 Windows 自包含发布包。首次 Android 构建需要 GitHub 下载 Gradle 与 Android 依赖。

## 连接方式

1. Android/Windows 自研客户端：WireGuard。
2. Windows/Android 系统 VPN 设置：IKEv2/IPsec。
3. FLClash：导入控制台显示的 `https://你的域名/sub/...yaml`。

所有配置文件与订阅 URL 都是连接凭据，不要截图、转发或上传公开仓库。
