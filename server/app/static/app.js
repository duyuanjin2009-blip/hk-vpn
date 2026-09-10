const $ = (selector) => document.querySelector(selector);
let devices = [];

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || `Request failed (${response.status})`);
  return response.headers.get("content-type")?.includes("application/json") ? response.json() : response.text();
}
function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c])); }
function copy(text) { navigator.clipboard.writeText(text).then(() => alert("已复制")); }
function diagnostic(label, ok, good, bad) {
  return '<article class="diagnostic ' + (ok ? 'ok' : 'bad') + '"><span>' + escapeHtml(label) + '</span><strong>' + (ok ? good : bad) + '</strong></article>';
}
function formatBytes(value) {
  const bytes = Math.max(0, Number(value) || 0); const units = ["B", "KB", "MB", "GB", "TB"]; let amount = bytes; let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit++; }
  return `${amount < 10 && unit ? amount.toFixed(1) : amount.toFixed(0)} ${units[unit]}`;
}
function ageText(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  if (value < 60) return `${value} 秒前`;
  if (value < 3600) return `${Math.floor(value / 60)} 分钟前`;
  return `${Math.floor(value / 3600)} 小时前`;
}
function dateText(seconds) {
  return seconds ? new Date(Number(seconds) * 1000).toLocaleString("zh-CN", {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"}) : "尚未开始";
}
function trafficSummaryView(summary) {
  if (!summary?.recordingSince) return '<p class="history-note">流量历史将在面板启动并采样后显示。</p>';
  return `<p class="history-note">近 24 小时记录：↑ ${formatBytes(summary.clientToServerBytes)}　↓ ${formatBytes(summary.serverToClientBytes)}<br><small>从 ${dateText(summary.recordingSince)} 开始记录</small></p>`;
}
function trafficHistoryView(history) {
  if (!history?.recordingSince) return '<p class="empty">尚未有样本。保持面板运行并在约一分钟后刷新。</p>';
  const points = history.points || []; const max = Math.max(1, ...points.map(point => Number(point.clientToServerBytes || 0) + Number(point.serverToClientBytes || 0)));
  const bars = points.map(point => { const total = Number(point.clientToServerBytes || 0) + Number(point.serverToClientBytes || 0); const height = total ? Math.max(6, Math.round(total / max * 100)) : 3; return `<i title="${dateText(point.recordedAt)}：↑ ${formatBytes(point.clientToServerBytes)} / ↓ ${formatBytes(point.serverToClientBytes)}" style="height:${height}%"></i>`; }).join("");
  return `<div class="history-controls"><button class="minor" data-traffic-range="24h">24 小时</button><button class="minor" data-traffic-range="7d">7 天</button><button class="minor" data-traffic-range="30d">30 天</button></div><p class="history-total">${history.range === "24h" ? "近 24 小时" : history.range}：↑ ${formatBytes(history.clientToServerBytes)}　↓ ${formatBytes(history.serverToClientBytes)}</p><div class="traffic-bars" aria-label="流量记录柱状图">${bars || '<span>等待更多样本</span>'}</div><p class="hint">${history.sampleCount} 个样本；柱形表示每段记录的总流量，非实时速度。</p>`;
}
function downloadConfig(text, filename) {
  // Android download managers may read a Blob URL after the click handler has
  // returned.  Do not revoke it immediately, or a valid .conf can be saved as
  // an empty/truncated file and WireGuard reports it as invalid.
  const url = URL.createObjectURL(new Blob([text], {type: "text/plain;charset=utf-8"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.hidden = true;
  document.body.appendChild(link);
  link.click();
  setTimeout(() => { link.remove(); URL.revokeObjectURL(url); }, 60000);
}
function connectionView(connection) {
  const state = connection?.state || "unknown";
  const traffic = `<p class="traffic">手机 → 服务器：${formatBytes(connection?.clientToServerBytes)}　服务器 → 手机：${formatBytes(connection?.serverToClientBytes)}<br><small>这是服务器端累计字节，不是实时网速。</small></p>`;
  if (state === "connected") return `<p class="connection ok">已双向连通 · 最近握手 ${ageText(connection.handshakeAgeSeconds)}</p>${traffic}`;
  if (state === "waiting") return `<p class="connection wait">正在建立隧道 · ${escapeHtml(connection.reason || "等待首次握手")}</p>${traffic}`;
  if (state === "idle") return `<p class="connection idle">当前没有活动 · 上次握手 ${ageText(connection.handshakeAgeSeconds)}</p>${traffic}`;
  if (state === "not-provisioned") return `<p class="connection bad">尚未同步到服务器 · ${escapeHtml(connection.reason || "请点击同步服务器")}</p>`;
  return `<p class="connection idle">连接状态未知 · ${escapeHtml(connection?.reason || "请刷新状态")}</p>`;
}
function deviceCard(device) {
  const state = device.revokedAt ? '<p class="error">已撤销</p>' : connectionView(device.connection);
  return `<article class="device"><div class="device-top"><div><h3>${escapeHtml(device.name)}</h3><span class="ip">${escapeHtml(device.wireGuardIp)}</span></div><span class="tag">${escapeHtml(device.platform)}</span></div>${state}${trafficSummaryView(device.trafficSummary)}${device.provisionError ? `<p class="error">待同步：${escapeHtml(device.provisionError)}</p>` : ''}<div class="actions"><button class="minor" data-action="details" data-id="${device.id}">查看配置</button>${!device.revokedAt ? `<button class="minor" data-action="rename" data-id="${device.id}">改节点名</button><button class="minor" data-action="sync" data-id="${device.id}">同步服务器</button><button class="danger" data-action="revoke" data-id="${device.id}">撤销</button>` : ''}</div></article>`;
}
function renderDiagnostics(data) {
  if (data.error) { $("#diagnostics").innerHTML = '<div class="empty">状态检测失败：' + escapeHtml(data.error) + '</div>'; return; }
  $("#diagnostics").innerHTML = [
    diagnostic("WireGuard 接口", data.wireguardInterface, "wg0 正常", "接口未启动"),
    diagnostic(`UDP ${data.wireguardListenPort || "?"}`, data.wireguardPort, "服务器正在监听", "服务器未监听"),
    diagnostic("IPv4 转发", data.ipForward, "已开启", "未开启"),
    diagnostic("转发规则", data.forwardRules, "wg0 规则正常", "缺少 wg0 规则"),
    diagnostic(`NAT 出口 ${data.wanInterface || "?"}`, data.nat, "MASQUERADE 正常", "缺少 NAT"),
    diagnostic("IKEv2", data.strongSwan, "StrongSwan 运行中", "服务未运行"),
  ].join("");
}
async function load() {
  const [health, diagnosticResult, deviceResult, protocolResult] = await Promise.all([api("/api/health"), api("/api/diagnostics"), api("/api/devices"), api("/api/protocols")]);
  devices = deviceResult.devices;
  renderDiagnostics(diagnosticResult);
  $("#status").textContent = `${health.wireGuardEndpoint} · ${devices.filter(d => !d.revokedAt).length} 台有效设备 · ${health.version || "旧版面板"}${deviceResult.connectionError ? " · 状态检测暂不可用" : ""}`;
  $("#devices").innerHTML = devices.length ? devices.map(deviceCard).join("") : '<div class="empty">还没有设备。点击“新增设备”开始。</div>';
  $("#protocols").innerHTML = protocolResult.nodes.length ? protocolResult.nodes.map(node => { const message = node.enabled ? (node.ready ? '已进入 FLClash 订阅' : '已启用，但当前从订阅隐藏：' + escapeHtml(node.reason || '服务未就绪')) : (node.ready ? '服务已监听，可开启' : escapeHtml(node.reason || '未部署')); return '<div class="protocol ' + (node.enabled ? '' : 'off') + '"><div><strong>' + escapeHtml(node.name) + '</strong><p>' + message + (node.port ? ' · 端口 ' + escapeHtml(node.port) : '') + '</p></div>' + (node.ready ? '<button data-protocol="' + escapeHtml(node.id) + '" data-enabled="' + node.enabled + '">' + (node.enabled ? '关闭' : '开启') + '</button>' : '<span class="tag">未就绪</span>') + '</div>'; }).join("") : '<p>目前只有每台设备独立的 WireGuard 节点。</p>';
}
async function showDetails(device) {
  const dialog = $("#detail-dialog"); $("#detail-title").textContent = device.name;
  const [ike, wg, history] = await Promise.all([api(`/api/devices/${device.id}/ikev2`), fetch(`/api/devices/${device.id}/wireguard.conf`).then(r => r.text()), api(`/api/devices/${device.id}/traffic?range=24h`)]);
  $("#detail-content").innerHTML = `<div class="detail-section"><h3>FLClash 订阅 URL</h3><div class="copy-row"><code>${escapeHtml(device.subscriptionUrl)}</code><button id="copy-sub">复制</button></div></div><div class="detail-section"><h3>流量记录</h3><div id="traffic-history">${trafficHistoryView(history)}</div></div><div class="detail-section"><h3>系统设置备用：IKEv2/IPsec</h3><p>服务器：<code>${escapeHtml(ike.server)}</code><br>类型：${escapeHtml(ike.type)}<br>用户名：<code>${escapeHtml(ike.username)}</code><br>密码：<code>${escapeHtml(ike.password)}</code></p><button id="copy-ike">复制账号信息</button></div><div class="detail-section"><h3>WireGuard 配置</h3><pre class="config">${escapeHtml(wg)}</pre><div class="actions"><button id="copy-wg">复制配置</button><button class="minor" id="download-wg">下载 .conf</button><button class="minor" id="show-qr">显示导入二维码</button></div><img id="wireguard-qr" class="wireguard-qr" alt="WireGuard 导入二维码" hidden></div>`;
  $("#copy-sub").onclick = () => copy(device.subscriptionUrl);
  $("#copy-ike").onclick = () => copy(`Server: ${ike.server}\nType: ${ike.type}\nUsername: ${ike.username}\nPassword: ${ike.password}`);
  $("#copy-wg").onclick = () => copy(wg);
  $("#download-wg").onclick = () => downloadConfig(wg, `hk-vpn-${device.id}.conf`);
  $("#show-qr").onclick = () => { const image = $("#wireguard-qr"); if (!image.src) image.src = `/api/devices/${device.id}/wireguard.png`; image.hidden = !image.hidden; };
  $("#traffic-history").onclick = async (event) => { const button = event.target.closest("button[data-traffic-range]"); if (!button) return; const box = $("#traffic-history"); box.innerHTML = '<p class="hint">正在读取流量记录…</p>'; try { const next = await api(`/api/devices/${device.id}/traffic?range=${button.dataset.trafficRange}`); box.innerHTML = trafficHistoryView(next); } catch (err) { box.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`; } };
  dialog.showModal();
}
$("#new-device").onclick = () => $("#device-dialog").showModal();
$("#refresh-status").onclick = async (event) => { const button = event.currentTarget; button.disabled = true; button.textContent = "正在检查…"; try { await load(); } catch (err) { alert(err.message); } finally { button.disabled = false; button.textContent = "刷新状态"; } };
$("#close-detail").onclick = () => $("#detail-dialog").close();
$("#device-form").addEventListener("submit", async (event) => { event.preventDefault(); const button = $("#create-device"); button.disabled = true; try { await api("/api/devices", {method:"POST",body:JSON.stringify({name: $("#device-name").value, platform: $("#device-platform").value})}); $("#device-dialog").close(); $("#device-form").reset(); await load(); } catch (err) { alert(err.message); } finally { button.disabled=false; } });
$("#devices").addEventListener("click", async (event) => { const button = event.target.closest("button[data-action]"); if (!button) return; const device = devices.find(d => d.id === button.dataset.id); try { if (button.dataset.action === "details") await showDetails(device); if (button.dataset.action === "rename") { const name = prompt("FLClash 节点名称", device.name); if (name?.trim()) { await api(`/api/devices/${device.id}`, {method:"PATCH",body:JSON.stringify({name})}); await load(); } } if (button.dataset.action === "sync") { await api(`/api/devices/${device.id}/reconcile`, {method:"POST"}); await load(); } if (button.dataset.action === "revoke" && confirm(`确定撤销 ${device.name} 吗？`)) { await api(`/api/devices/${device.id}/revoke`, {method:"POST"}); await load(); } } catch (err) { alert(err.message); } });
$("#protocols").addEventListener("click", async (event) => { const button = event.target.closest("button[data-protocol]"); if (!button) return; const enabling = button.dataset.enabled === "false"; if (enabling && !confirm("确认对应服务器协议已经部署、密码和证书已替换并测试成功？")) return; try { await api(`/api/protocols/${encodeURIComponent(button.dataset.protocol)}`, {method:"PATCH",body:JSON.stringify({enabled: enabling})}); await load(); } catch (err) { alert(err.message); } });
load().catch(err => { $("#status").textContent = `加载失败：${err.message}`; });
