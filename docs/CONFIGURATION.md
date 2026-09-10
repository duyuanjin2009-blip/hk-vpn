# 完整配置方案

## 架构与职责

服务器运行四类互不冲突的服务：

| 服务 | 端口 | 用途 | 客户端 |
|---|---:|---|---|
| Baota Nginx + HK VPN Panel | TCP 443 | 控制台、原生客户端取配置、FLClash 订阅 | 浏览器、Android、Windows、FLClash |
| WireGuard | UDP 51820 | 主 VPN 隧道 | Android/Windows 自研客户端、WireGuard、FLClash |
| StrongSwan IKEv2 | UDP 500、4500 | 系统设置备用隧道 | Windows 原生 VPN、支持 IKEv2 的 Android 系统 |
| 可选代理模块 | 见下表 | FLClash 的额外备用节点 | FLClash TUN Mode |

网页不转发任何用户流量。它只下发配置、保存设备记录和调用受限的本机管理助手；流量由 WireGuard、IKEv2 或启用的代理模块直接处理。

## 域名与 DNS

最简洁的方式是使用一个域名，例如 `vpn.example.com`。把它的 A 记录指向香港服务器的公网 IPv4。控制台、订阅、WireGuard 和 IKEv2 可以共用这个域名，因为协议端口不同。

如果想把入口区分得更清楚，可以使用：

```text
panel.example.com  网页控制台与原生客户端 API
sub.example.com    FLClash 订阅（可选；需为 panel 添加一份 Nginx 站点）
vpn.example.com    WireGuard、IKEv2 和其他协议节点
```

本项目的默认部署用一个域名；`HKVPN_PUBLIC_URL` 必须与浏览器真正访问的 HTTPS 地址完全一致。

## 三种连接方式

### 1. 自研客户端：WireGuard 主连接

Android 与 Windows 客户端在首次使用时输入控制台地址与管理密码。服务端创建独立设备记录、WireGuard 密钥、地址和 IKEv2 账号。之后客户端把 WireGuard 配置保存在本地，日常连接不再请求控制台。

- WireGuard 服务器：`10.88.0.1/24`
- 客户端地址池：`10.88.0.2` 至 `10.88.0.254`
- 全局流量：`0.0.0.0/0, ::/0`
- DNS：默认 `1.1.1.1`、`1.0.0.1`，可在 `/etc/hk-vpn/panel.env` 修改。
- Windows 首次连接需要安装官方 WireGuard；程序会请求管理员权限创建 Windows 隧道服务。
- Android 首次连接会显示系统 VPN 授权弹窗，这是 Android 的正常限制。

#### Android 设备适配

Android 客户端的 `minSdk` 为 23，因此覆盖 Android 6.0 及以上的手机、平板和常见国产 ROM。界面按 dp 而非固定像素排版：小屏手机保留 20dp 边距，大屏/平板将内容限制在 560dp 宽并居中，横竖屏切换后会保留已输入的控制台地址。APK 内含 WireGuard 的常用 ARM/ARM64/x86/x86_64 原生库；Android 会只安装与设备 CPU 匹配的部分。

首次连接前客户端会主动请求系统 VPN 权限。Android 10 及以上、华为/小米/OPPO/vivo 等系统若有“电池优化”“后台限制”选项，建议将 HK VPN 设为不受限制，避免锁屏后后台网络被系统回收。系统 VPN 备用的 IKEv2 菜单在部分 Android ROM 中可能被厂商移除，不影响主 WireGuard 连接。

### 2. 系统设置备用：IKEv2/IPsec

每个设备在控制台中都有独立的 IKEv2 用户名和密码。服务器使用公开受信任的 Baota TLS 证书认证自身，因此客户端填写的是域名而不是 IP。

Windows：设置 → 网络和 Internet → VPN → 添加 VPN → **Windows（内置）** → IKEv2 → 用户名和密码。

Android：设置 → 网络和 Internet → VPN → 添加 VPN → 选择类似 **IKEv2/IPsec EAP** 的类型。不同品牌的 Android 10 ROM 可能没有这个选项；此时使用自研 Android 客户端即可。

IKEv2 地址池为 `10.89.0.0/24`。部署脚本已经为这两个私网地址池添加 NAT 与转发规则。

### 3. FLClash：订阅 URL

在控制台新增一个 `FLClash` 类型设备，复制其订阅 URL，在 FLClash 选择“URL 导入”。订阅内容是 Mihomo YAML，默认包含该设备的独立 WireGuard 节点；启用附加协议后，同一订阅会多出对应节点和“Hong Kong Manual / Hong Kong Auto”分组。

控制台里的名称是订阅中节点名称，不是 FLClash 自己的“配置文件标题”。如果 FLClash 把 URL 配置显示成一串随机字符，请在 FLClash 内改配置标题；在控制台点击“改节点名”可改 `香港主节点 · WireGuard` 这一类节点名称。

每张设备卡的流量数字均从服务器视角读取：`手机 → 服务器` 等于服务器收到的 WireGuard 字节，`服务器 → 手机` 等于服务器发出的字节。它们是累计值，因此刚连接时不能拿来当速度表。以“最近握手”判断是否真正连通。

订阅 URL 长期有效、不需要配对码。它内含可连接的信息，等同于密码；泄露后直接在控制台撤销该设备并重新新建设备。

## FLClash 协议模块

`/etc/hk-vpn/protocols.json` 决定哪些节点被写进所有 FLClash 订阅。初始文件是 `server/scripts/protocols.example.json`，所有可选节点默认 `enabled: false`，避免虚假节点出现在订阅中。

| 模块 | 建议端口 | 说明 |
|---|---:|---|
| Hysteria2 | UDP 8443 | QUIC 代理，适合移动网络；启用后写入 FLClash。 |
| TUIC | UDP 8444 | 另一条 QUIC 备用。 |
| Shadowsocks 2022 | TCP/UDP 8388 | 简单兼容代理。 |
| VLESS | TCP 443 或独立端口 | 需要与 Nginx/Xray 的 TLS 路由协调。 |
| Trojan | TCP 443 或独立端口 | 同样需要 TLS 路由协调。 |
| OpenVPN | UDP 1194 | 传统客户端兼容。 |

不要把 `enabled` 改为 `true`，除非对应服务器服务已经部署、证书与密码已替换并完成测试。每个高级节点还必须填写实际的 `service` systemd 单元名；面板会同时检查必填字段、本机 TCP/UDP 监听和该服务是否运行。服务没启动时不能启用；已启用的服务后来停止时，订阅会自动隐藏该节点而保留 WireGuard。Mihomo 还支持更多节点格式；本项目的 `protocols.json` 是扩展点，增加一个有效 YAML `config` 对象即可写进订阅。对于 Snell、Tailscale、ZeroTier 等需要第三方控制面或专有软件的类型，后台可以导入上游节点，但不会声称由这台 VPS 自建。

## 管理与恢复

系统角色分为：

- `hkvpn`：只运行网页控制台，可读环境配置、可写 SQLite 数据库。
- `root`：运行 `/usr/local/libexec/hk-vpn/vpnctl.py`，只接受添加、撤销、重建设备三种固定操作。
- `wg0`：只保留基础接口；设备 Peer 从 `/etc/hk-vpn/devices.json` 在启动时恢复。

主要状态文件：

```text
/var/lib/hk-vpn-panel/panel.db        控制台设备与订阅记录
/etc/hk-vpn/devices.json              root 侧 WireGuard / IKEv2 对应记录
/etc/wireguard/wg0.conf               WireGuard 基础接口与 NAT 规则
/etc/swanctl/conf.d/hk-vpn.conf       IKEv2 连接定义
/etc/swanctl/conf.d/hk-vpn-users.conf IKEv2 用户密码（自动生成）
```

备份时将上述文件复制到离线、加密位置。恢复后依次执行：

```bash
systemctl restart wg-quick@wg0
/usr/local/libexec/hk-vpn/vpnctl.py reconcile
systemctl restart hk-vpn-panel
```

## 最少维护操作

```bash
systemctl status hk-vpn-panel wg-quick@wg0 strongswan-swanctl
wg show
swanctl --list-sas
journalctl -u hk-vpn-panel -n 80 --no-pager
```

控制台“撤销”会移除 WireGuard Peer、删除 IKEv2 账号并让 FLClash 订阅失效。已导入的旧配置也会因为服务器端 Peer 被删除而不能重新握手。
