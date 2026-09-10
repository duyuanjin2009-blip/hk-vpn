# HK VPN Routing v2：全新部署

此版本的首要目标是稳定的全局 WireGuard 出网，并提供 FLClash 可导入订阅和官方 WireGuard `.conf` 备用导入。它不会把没有实际部署和验证的高级协议写进 FLClash 订阅。

> 全新部署会重新生成服务器密钥和设备资料。旧 WireGuard / FLClash 配置将失效；请先在宝塔中保留现有证书和站点，再按下面步骤执行。

## 1. 放行端口

在云厂商安全组和宝塔“安全”页都放行：80/TCP、443/TCP、51820/UDP。若要使用 IKEv2，再放行 500/UDP、4500/UDP。

## 2. 获取源码并部署

在宝塔终端以 root 执行。把域名和面板密码替换为自己的值；密码仅保存在服务器 `/etc/hk-vpn/panel.env`，不要提交到 GitHub。

```bash
cd /root
git clone https://github.com/duyuanjin2009-blip/hk-vpn.git hk-vpn-routing-v2
cd /root/hk-vpn-routing-v2
chmod +x server/scripts/deploy-bt.sh
./server/scripts/deploy-bt.sh --domain vpn.duyuanjin2009.cc --admin-password '自己的面板密码'
```

部署会安装 `wg-routing.sh`。每次 `wg0` 启动都会：开启 IPv4 转发、按服务器当前默认出口安装 `10.88.0.0/24` 的 NAT、插入双向 FORWARD 规则，并放行 UDP 51820。规则以插入方式放在 Docker/宝塔的拒绝规则前方，避免“已握手但不能上网”。

## 3. 验证服务器

```bash
systemctl is-active wg-quick@wg0 hk-vpn-panel
wg show wg0
iptables -nvL FORWARD | grep wg0
iptables -t nat -nvL POSTROUTING | grep 10.88
```

前两项都应为 `active`。新设备连接并访问网页后，最后两项的计数应增加。

## 4. 宝塔 HTTPS

在宝塔添加 `vpn.duyuanjin2009.cc` 网站，签发 SSL 证书后，把 `server/nginx/hk-vpn-panel.conf.template` 中的 `__DOMAIN__` 替换为该域名，保存并重载 Nginx。

打开 `https://vpn.duyuanjin2009.cc` 登录面板，创建一个新的 Android 设备。

## 5. 客户端使用顺序

1. 先用设备详情中的二维码或 `.conf` 测试官方 WireGuard；同一份配置不要同时在 WireGuard 和 FLClash 中启用。
2. 再复制“FLClash 订阅 URL”到 FLClash。订阅只包含 WireGuard 节点、TUN 和 DNS 配置。
3. FLClash 必须授予 Android VPN 权限，状态栏出现 VPN 钥匙图标后，应用流量才会进入隧道。

若 WireGuard 已握手但无法上网，连接状态下执行第 3 步的两条 iptables 命令。若计数不增长，客户端没有把系统流量导入隧道；若计数增长但无响应，请保存完整输出后检查服务器默认路由和云厂商出站策略。
