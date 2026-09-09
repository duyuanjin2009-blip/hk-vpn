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

### WireGuard / 自研客户端

Android 或 Windows 程序首次输入 `https://vpn.example.com` 和管理密码，点击 Connect。Windows 需要先从 WireGuard 官方安装程序安装 WireGuard；Android 会出现系统 VPN 授权。

也可以在控制台详情下载 WireGuard 配置，用官方客户端手工导入作为排错方式。

### IKEv2 系统设置

控制台详情复制 IKEv2 信息。Windows 选择“Windows（内置）→ IKEv2 → 用户名和密码”；Android 选择系统提供的 IKEv2/IPsec EAP 类型。连接失败时检查：

```bash
swanctl --list-sas
journalctl -u strongswan-swanctl -n 100 --no-pager
```

### FLClash URL

控制台详情复制 `subscriptionUrl`。FLClash 添加 URL 配置，选择节点后启用 TUN Mode。默认只有 WireGuard 节点；不要在协议服务没部署前启用 JSON 中的可选节点。

## 5. 启用附加 FLClash 协议

每一种协议都必须先部署对应服务、替换 `protocols.json` 的示例密码/UUID/域名、用独立客户端测试成功，最后才将该模块的 `enabled` 改为 `true`。修改后刷新 FLClash 订阅即可。

对 443/TCP 上的 VLESS、Trojan 和网页 HTTPS，必须使用 Nginx stream/SNI 分流或独立端口；不要让两个程序直接争抢同一个端口。对 Hysteria2 若使用 UDP 443，则不要让 Nginx 启用 HTTP/3；默认 UDP 8443 可避免冲突。

## 6. 更新与回滚

更新前先备份状态文件，再把新版本上传到 `/root` 并重新运行安装脚本。部署脚本不会主动删除已有 SQLite 数据库或设备状态。

```bash
cp -a /var/lib/hk-vpn-panel /root/hk-vpn-backup
cp -a /etc/hk-vpn /root/hk-vpn-system-backup
systemctl restart hk-vpn-panel
```

若更新出错，恢复备份、重新启动 `wg-quick@wg0` 和 `hk-vpn-panel` 即可。
