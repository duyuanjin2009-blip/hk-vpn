# 宝塔端口配置（HK VPN）

本配置适用于 Debian 12、宝塔 Nginx、HK VPN Suite。将下面的规则同时添加到：

1. 云厂商安全组 / 防火墙；
2. 宝塔面板 → 安全 → 系统防火墙。

两处都要放行；只放行其中一处仍可能无法连接。

## 一、基础必开规则

在宝塔“安全”页面依次点击“添加端口规则”，填写下表。来源 IP 选择“所有 IP”，备注按表填写即可。

| 协议 | 端口 | 宝塔备注 | 作用 |
|---|---:|---|---|
| TCP | 80 | HK VPN HTTP / 证书 | Let’s Encrypt 验证与 HTTP 跳转 |
| TCP | 443 | HK VPN Panel | 网页控制台、订阅 URL、Android/Windows 取配置 |
| UDP | 51820 | HK VPN WireGuard | 主 VPN 隧道 |
| UDP | 500 | HK VPN IKEv2 | IKEv2/IPsec 协商 |
| UDP | 4500 | HK VPN IKEv2 NAT-T | IKEv2 穿透 NAT；必须开放 |

可直接按下面的顺序添加：

```text
TCP   80       HK VPN HTTP / Certificate
TCP   443      HK VPN Panel and Subscription
UDP   51820    HK VPN WireGuard
UDP   500      HK VPN IKEv2
UDP   4500     HK VPN IKEv2 NAT-T
```

不要开放 `8787`：它仅监听服务器本机，由宝塔 Nginx 反向代理到 HTTPS。

不要向公网开放宝塔面板端口 `7810`。宝塔设置 → 安全入口中更换随机安全入口，并将面板防火墙来源限制为自己的常用公网 IP。

## 二、可选 FLClash 协议规则

仅在对应服务已经安装、账号/密码/证书配置完成，并在本机测试成功后，才添加规则与在 HK VPN 控制台启用节点。未部署的协议不要开放端口。

| 协议模块 | 需要添加的规则 | 默认用途 |
|---|---|---|
| Hysteria2 | UDP 8443 | 移动网络备用代理 |
| TUIC | UDP 8444 | QUIC 备用代理 |
| Shadowsocks 2022 | TCP 8388、UDP 8388 | 通用代理节点 |
| OpenVPN | UDP 1194 | 传统客户端备用 |
| VLESS / Trojan | 通常复用 TCP 443 | 必须先做 SNI/stream 分流，不能与普通 Nginx HTTPS 直接抢占 443 |

对应的宝塔规则文本：

```text
UDP   8443     Hysteria2 (only when enabled)
UDP   8444     TUIC (only when enabled)
TCP   8388     Shadowsocks 2022 (only when enabled)
UDP   8388     Shadowsocks 2022 UDP (only when enabled)
UDP   1194     OpenVPN (only when enabled)
```

## 三、宝塔 Nginx 站点配置

1. 宝塔 → 网站 → 添加站点，填写 VPN 域名，例如 `vpn.example.com`。
2. SSL → Let’s Encrypt，签发证书并打开“强制 HTTPS”。
3. 将项目中的 `server/nginx/hk-vpn-panel.conf.template` 里所有 `__DOMAIN__` 替换为真实域名，粘贴到站点配置中。
4. 重载 Nginx。

Nginx 只使用 TCP 80 和 443；WireGuard、IKEv2、Hysteria2、TUIC 都不经过 Nginx。

## 四、部署后核查

服务器上执行：

```bash
systemctl status hk-vpn-panel wg-quick@wg0 strongswan-swanctl
wg show
ss -lntup | grep -E ':(80|443|51820|500|4500|8787)\\b'
curl http://127.0.0.1:8787/api/health
```

预期：

- `8787` 只显示 `127.0.0.1:8787`，不应是 `0.0.0.0:8787`；
- `51820/udp` 由 WireGuard 监听；
- `500/udp`、`4500/udp` 由 StrongSwan 监听；
- 域名访问 `https://你的域名/login` 返回登录页。

若外网仍无法连接，优先检查云厂商安全组；它通常在宝塔防火墙之前拦截流量。
