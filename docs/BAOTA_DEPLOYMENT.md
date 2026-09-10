# 宝塔 Debian 12 部署方案

以下步骤针对已经能 SSH 登录的 Debian 12 + 宝塔服务器。开始前确认：域名的 A 记录已经指向服务器公网 IP；宝塔 Nginx 正常运行；服务器有足够权限安装系统软件。

## 0. 端口与面板预处理

在云厂商安全组和宝塔“安全”页面同时放行：

| 端口 | 协议 | 用途 |
|---:|---|---|
| 80、443 | TCP | 宝塔签发证书与控制台 |
| 51820 | UDP | WireGuard |
| 500、4500 | UDP | IKEv2/IPsec |
| 8443 | UDP | Hysteria2（仅启用后） |
| 8444 | UDP | TUIC（仅启用后） |
| 8388 | TCP、UDP | Shadowsocks 2022（仅启用后） |
| 1194 | UDP | OpenVPN（仅启用后） |

不要在公网长期暴露宝塔面板端口 `7810`。在宝塔设置中修改安全入口、设置强密码与二次验证，并在防火墙中只允许你的常用 IP 访问。

## 1. 上传项目并运行基础安装

把 GitHub Release 或 Actions 下载的源码包上传到服务器，例如 `/root/hk-vpn-suite`，解压后执行：

```bash
cd /root/hk-vpn-suite/server/scripts
chmod +x deploy-bt.sh configure-ikev2-cert.sh
./deploy-bt.sh --domain vpn.example.com --admin-password '改成你自己的长密码'
```

脚本会安装 WireGuard、StrongSwan、Python，创建 `hkvpn` 系统用户，建立 `wg0`，开启 IPv4 转发，并启动网页控制台监听 `127.0.0.1:8787`。

检查基础服务：

```bash
systemctl status hk-vpn-panel wg-quick@wg0
wg show
curl http://127.0.0.1:8787/login
```

若 `wg0` 无法启动，先确认 `ip -4 route list default` 有默认路由，再查看：

```bash
journalctl -u wg-quick@wg0 -n 100 --no-pager
```

### 完整重装

仅当需要让所有旧设备、订阅 URL 和服务器密钥失效时使用。脚本会先将旧状态备份到 `/root/hk-vpn-backup-日期时间.tar.gz`，再删除 HK VPN 自己的状态文件并重新部署；不会删除宝塔、网站文件或证书。密码会在服务器终端交互输入，不会写入 GitHub。

```bash
cd /root/hk-vpn-suite/server/scripts
chmod +x reinstall.sh
./reinstall.sh --domain vpn.example.com --confirm-reset
```

## 2. 在宝塔签发证书并反向代理

1. 宝塔网站 → 添加站点，域名填写 `vpn.example.com`，网站目录可任意空目录。
2. SSL 页面使用 Let’s Encrypt 签发证书并打开“强制 HTTPS”。
3. 网站配置文件中，将 `server/nginx/hk-vpn-panel.conf.template` 的所有 `__DOMAIN__` 替换为你的域名后保存。
4. 在宝塔 Nginx 管理页重载 Nginx。

验证：

```bash
curl -I https://vpn.example.com/login
```

浏览器打开 `https://vpn.example.com`，用部署时设置的管理密码登录。先新建一个测试设备；此时如果详情显示“待同步”，执行一次“同步服务器”，再检查 `wg show`。

## 3. 让 IKEv2 使用宝塔证书

宝塔证书签发完成后执行：

```bash
/opt/hk-vpn-suite/server/scripts/configure-ikev2-cert.sh vpn.example.com
swanctl --list-conns
```

若证书续期，请在宝塔计划任务中每日执行一次同一命令。它会复制新的证书与私钥到 StrongSwan 目录并重新加载连接，不需要重启 Nginx。

## 4. 验证三种连接方式

### WireGuard（推荐）

网页控制台新建设备后，下载 WireGuard 配置并导入官方 WireGuard 客户端。这是排错和日常连接最稳定的方式。

### IKEv2 系统设置

控制台详情复制 IKEv2 信息。Windows 选择“Windows（内置）→ IKEv2 → 用户名和密码”；Android 选择系统提供的 IKEv2/IPsec EAP 类型。连接失败时检查：

```bash
swanctl --list-sas
journalctl -u strongswan-swanctl -n 100 --no-pager
```

### FLClash URL

控制台详情复制 `subscriptionUrl`。FLClash 添加 URL 配置后，选择节点并打开客户端的 VPN/TUN 功能。默认只有 WireGuard 节点；不要在协议服务没部署前启用 JSON 中的可选节点。控制台设备卡片的“改节点名”会更新订阅里的 WireGuard 节点名；FLClash 自己显示的订阅配置标题仍可能需要在 FLClash 内重命名。

连接后点击网页的“刷新状态”。设备卡片会明确显示：

- **正在建立隧道**：服务端尚未看到首次 WireGuard 握手；刚打开 FLClash 时可等待数秒。
- **已双向连通**：最近 3 分钟内存在握手。
- **当前没有活动**：配置仍在，但超过 3 分钟没有新握手。
- **手机 → 服务器 / 服务器 → 手机**：这是服务端的累计 WireGuard 字节方向，**不是实时网速**，不可用来判断刚连接瞬间的速度。

FLClash 的不同内核版本支持的节点类型不完全一致。若 FLClash 显示 `unsupported proxy type: openvpn`，说明它拒绝了整个订阅中的 OpenVPN 节点；关闭该节点并更新订阅即可恢复 WireGuard。OpenVPN 应使用官方 OpenVPN 客户端独立导入，不写入 FLClash 订阅。

## 5. 启用附加 FLClash 协议

每一种协议都必须先部署对应服务、替换 `protocols.json` 的示例密码/UUID/域名和 `service`（真实 systemd 单元名）、用独立客户端测试成功，最后才将该模块的 `enabled` 改为 `true`。网页会同时检查对应 TCP/UDP 端口是否正由本机监听，以及该 systemd 服务是否在运行；两项任一失败，节点不能开启，已开启后服务停止时会自动从订阅隐藏，WireGuard 节点仍会保留。

对 443/TCP 上的 VLESS、Trojan 和网页 HTTPS，必须使用 Nginx stream/SNI 分流或独立端口；不要让两个程序直接争抢同一个端口。对 Hysteria2 若使用 UDP 443，则不要让 Nginx 启用 HTTP/3；默认 UDP 8443 可避免冲突。

## 6. 安全更新网页控制台（不会重置 VPN）

**不要为了更新网页界面重新运行 `deploy-bt.sh`。** 基础安装脚本会生成新的 WireGuard 服务器密钥，旧设备会失效。

从 GitHub 拉取新版源码后，仅执行下面的更新脚本。它只更新网页、服务程序和受限管理助手，保留 `/etc/wireguard/wg0.conf`、`/etc/hk-vpn/`、已创建设备和订阅 URL，且不会重启 `wg0`：

```bash
cd /root/hk-vpn-suite
git pull
chmod +x server/scripts/update-panel.sh
./server/scripts/update-panel.sh
systemctl status hk-vpn-panel --no-pager
```

刷新浏览器后可看到“服务状态与端口检测”。它会显示 WireGuard 接口和实际 UDP 监听端口、IPv4 转发、wg0 转发规则、NAT 规则、StrongSwan 服务以及每设备的握手状态；它不能替代外网 UDP 实测。页面顶端会显示版本，例如 `2026.09.10-traffic`；若显示“旧版面板”，表示服务器尚未更新成功或浏览器仍在展示旧页面。

新版会每 5 分钟记录一次每台设备的累计 WireGuard 字节，并保留 31 天；打开设备详情可查看 24 小时、7 天或 30 天流量。首次更新后先连接设备、等待至少 1 分钟并点击“刷新状态”，第二个样本出现后才会有可计算的流量记录。

设备详情还提供 WireGuard `.conf` 下载和导入二维码。二维码包含该设备私钥，只能在自己的设备上展示，使用后关闭详情页，不要截图或转发。

## 7. 回滚

更新前先备份状态文件，再把新版本上传到 `/root` 并重新运行安装脚本。部署脚本不会主动删除已有 SQLite 数据库或设备状态。

```bash
cp -a /var/lib/hk-vpn-panel /root/hk-vpn-backup
cp -a /etc/hk-vpn /root/hk-vpn-system-backup
systemctl restart hk-vpn-panel
```

若更新出错，恢复备份后只重启 `hk-vpn-panel`。除非 WireGuard 本身损坏，否则不需要重启 `wg0`。
