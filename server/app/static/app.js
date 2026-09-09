const $ = (selector) => document.querySelector(selector);
let devices = [];

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || `Request failed (${response.status})`);
  return response.headers.get("content-type")?.includes("application/json") ? response.json() : response.text();
}
function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c])); }
function copy(text) { navigator.clipboard.writeText(text).then(() => alert("已复制")); }
async function load() {
  const [health, deviceResult, protocolResult] = await Promise.all([api("/api/health"), api("/api/devices"), api("/api/protocols")]);
  devices = deviceResult.devices;
  $("#status").textContent = `${health.wireGuardEndpoint} · ${devices.filter(d => !d.revokedAt).length} 台有效设备`;
  $("#devices").innerHTML = devices.length ? devices.map(device => `<article class="device"><div class="device-top"><div><h3>${escapeHtml(device.name)}</h3><span class="ip">${device.wireGuardIp}</span></div><span class="tag">${escapeHtml(device.platform)}</span></div>${device.revokedAt ? '<p class="error">已撤销</p>' : ''}${device.provisionError ? `<p class="error">待同步：${escapeHtml(device.provisionError)}</p>` : ''}<div class="actions"><button class="minor" data-action="details" data-id="${device.id}">查看配置</button>${!device.revokedAt ? `<button class="minor" data-action="sync" data-id="${device.id}">同步服务器</button><button class="danger" data-action="revoke" data-id="${device.id}">撤销</button>` : ''}</div></article>`).join("") : '<div class="empty">还没有设备。点击“新增设备”开始。</div>';
  $("#protocols").innerHTML = protocolResult.nodes.length ? protocolResult.nodes.map(node => `<div class="protocol ${node.enabled ? '' : 'off'}"><span>${escapeHtml(node.config.name || node.id)}</span><button data-protocol="${escapeHtml(node.id)}" data-enabled="${node.enabled}">${node.enabled ? '已启用' : '启用'}</button></div>`).join("") : '<p>目前只有每台设备独立的 WireGuard 节点。部署可选协议后会在这里显示。</p>';
}
async function showDetails(device) {
  const dialog = $("#detail-dialog"); $("#detail-title").textContent = device.name;
  const [ike, wg] = await Promise.all([api(`/api/devices/${device.id}/ikev2`), fetch(`/api/devices/${device.id}/wireguard.conf`).then(r => r.text())]);
  $("#detail-content").innerHTML = `<div class="detail-section"><h3>FLClash 订阅 URL</h3><div class="copy-row"><code>${escapeHtml(device.subscriptionUrl)}</code><button id="copy-sub">复制</button></div></div><div class="detail-section"><h3>系统设置备用：IKEv2/IPsec</h3><p>服务器：<code>${escapeHtml(ike.server)}</code><br>类型：${escapeHtml(ike.type)}<br>用户名：<code>${escapeHtml(ike.username)}</code><br>密码：<code>${escapeHtml(ike.password)}</code></p><button id="copy-ike">复制账号信息</button></div><div class="detail-section"><h3>WireGuard 配置</h3><pre class="config">${escapeHtml(wg)}</pre><button id="copy-wg">复制配置</button></div>`;
  $("#copy-sub").onclick = () => copy(device.subscriptionUrl);
  $("#copy-ike").onclick = () => copy(`Server: ${ike.server}\nType: ${ike.type}\nUsername: ${ike.username}\nPassword: ${ike.password}`);
  $("#copy-wg").onclick = () => copy(wg);
  dialog.showModal();
}
$("#new-device").onclick = () => $("#device-dialog").showModal();
$("#close-detail").onclick = () => $("#detail-dialog").close();
$("#device-form").addEventListener("submit", async (event) => { event.preventDefault(); const button = $("#create-device"); button.disabled = true; try { await api("/api/devices", {method:"POST",body:JSON.stringify({name: $("#device-name").value, platform: $("#device-platform").value})}); $("#device-dialog").close(); $("#device-form").reset(); await load(); } catch (err) { alert(err.message); } finally { button.disabled=false; } });
$("#devices").addEventListener("click", async (event) => { const button = event.target.closest("button[data-action]"); if (!button) return; const device = devices.find(d => d.id === button.dataset.id); try { if (button.dataset.action === "details") await showDetails(device); if (button.dataset.action === "sync") { await api(`/api/devices/${device.id}/reconcile`, {method:"POST"}); await load(); } if (button.dataset.action === "revoke" && confirm(`确定撤销 ${device.name} 吗？`)) { await api(`/api/devices/${device.id}/revoke`, {method:"POST"}); await load(); } } catch (err) { alert(err.message); } });
$("#protocols").addEventListener("click", async (event) => { const button = event.target.closest("button[data-protocol]"); if (!button) return; const enabling = button.dataset.enabled === "false"; if (enabling && !confirm("确认对应服务器协议已经部署、密码和证书已替换并测试成功？")) return; try { await api(`/api/protocols/${encodeURIComponent(button.dataset.protocol)}`, {method:"PATCH",body:JSON.stringify({enabled: enabling})}); await load(); } catch (err) { alert(err.message); } });
load().catch(err => { $("#status").textContent = `加载失败：${err.message}`; });
