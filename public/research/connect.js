"use strict";
// Model connection: lets each visitor choose how the AI features reach Claude or Codex/OpenAI.
//   server      the site's own key (if the operator configured one)
//   cli         the operator's local Claude Code / Codex subscription (serve_local.py only)
//   anthropic   the visitor's own Claude API key, called straight from the browser
//   openai      the visitor's own OpenAI key (GPT / Codex models), called straight from the browser
// (OpenAI support is kept in the code paths below but not offered in the dialog.)
// Visitor keys stay in this browser (localStorage, or sessionStorage if "remember" is off)
// and are sent only to api.anthropic.com / api.openai.com, never to this website.
const CONN_KEY = "airg.connect.v1";
// Claude models offered in the Q&A page. `id` is the Claude Code alias, `api` the API model id.
const CLAUDE_CHOICES = [
  { id: "sonnet", api: "claude-sonnet-5-5", label: "Sonnet 5.5", note: "均衡，推荐" },
  { id: "opus", api: "claude-opus-5-5", label: "Opus 5.5", note: "最强，较慢、更耗额度" },
  { id: "haiku", api: "claude-haiku-5-5", label: "Haiku 5.5", note: "最快、最省额度" },
];
const MODELS = {
  anthropic: ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
  openai: ["gpt-5-codex", "gpt-5", "gpt-5-mini", "gpt-4.1-mini"],
};
const Connect = {
  cfg: { claudeModel: "sonnet", mode: "", anthropicKey: "", anthropicModel: MODELS.anthropic[0], openaiKey: "", openaiModel: MODELS.openai[0], remember: true, cliProvider: "", serverProvider: "" },
  server: null,
  load() {
    for (const store of [localStorage, sessionStorage]) {
      try { const v = JSON.parse(store.getItem(CONN_KEY) || "null"); if (v) { Object.assign(this.cfg, v); break; } } catch { /* storage unavailable */ }
    }
  },
  save() {
    try {
      const data = JSON.stringify(this.cfg);
      if (this.cfg.remember) { localStorage.setItem(CONN_KEY, data); sessionStorage.removeItem(CONN_KEY); }
      else { sessionStorage.setItem(CONN_KEY, data); localStorage.removeItem(CONN_KEY); }
    } catch { /* storage unavailable */ }
    updateConnBadge();
  },
  async serverInfo() {
    if (this.server) return this.server;
    try { const r = await fetch("/api/ask"); const j = r.ok ? await r.json() : {}; this.server = j.providers || []; } catch { this.server = []; }
    return this.server;
  },
  async localStatus(check = false) {
    try {
      const r = await fetch(`/api/claude/status${check ? "?check=1" : ""}`, { headers: { "x-airg": "1" } });
      return r.ok ? await r.json() : null;
    } catch { return null; }
  },
  cliOptions() { return (this.server || []).filter((p) => p.id === "claude-cli"); },
  apiOptions() { return (this.server || []).filter((p) => p.id === "anthropic"); },
  // The connection actually used right now, or null.
  active() {
    const c = this.cfg; const srv = this.server || [];
    if (c.mode === "anthropic" && c.anthropicKey) return { mode: "anthropic", label: "Claude（我的 key）", model: this.choice().label };
    if (c.mode === "cli") { const p = srv.find((x) => x.id === c.cliProvider) || this.cliOptions()[0]; if (p) return { mode: "cli", provider: p.id, label: "Claude（邮箱登录）", model: this.choice().label }; }
    if (c.mode === "server" || !c.mode) { const p = srv.find((x) => x.id === c.serverProvider) || this.apiOptions()[0] || (!c.mode ? this.cliOptions()[0] : null); if (p) return { mode: p.id.endsWith("-cli") ? "cli" : "server", provider: p.id, label: p.label, model: p.model }; }
    return null;
  },
  choice() { return CLAUDE_CHOICES.find((m) => m.id === this.cfg.claudeModel) || CLAUDE_CHOICES[0]; },
  setModel(id) { if (CLAUDE_CHOICES.some((m) => m.id === id)) { this.cfg.claudeModel = id; this.cfg.anthropicModel = this.choice().api; this.save(); } },
  // What to send as `model` to /api/ask for the active connection.
  requestModel() { const a = this.active(); if (!a) return ""; return a.mode === "cli" ? this.choice().id : this.choice().api; },
  isDirect() { const a = this.active(); return a && (a.mode === "anthropic" || a.mode === "openai"); },

  // ---- direct browser calls (visitor's own key) ------------------------------------------
  _req(system, messages, maxTokens, stream) {
    const a = this.active();
    if (a.mode === "anthropic") {
      return fetch("https://api.anthropic.com/v1/messages", { method: "POST",
        headers: { "x-api-key": this.cfg.anthropicKey, "anthropic-version": "2023-06-01", "content-type": "application/json", "anthropic-dangerous-direct-browser-access": "true" },
        body: JSON.stringify({ model: this.choice().api, max_tokens: maxTokens, system, messages, stream }) });
    }
    return fetch("https://api.openai.com/v1/responses", { method: "POST",
      headers: { authorization: `Bearer ${this.cfg.openaiKey}`, "content-type": "application/json" },
      body: JSON.stringify({ model: this.cfg.openaiModel, instructions: system, input: messages, max_output_tokens: maxTokens, stream }) });
  },
  async _err(res) {
    const j = await res.json().catch(() => ({}));
    const msg = typeof j.error === "string" ? j.error : j.error?.message;
    const hint = res.status === 401 ? "（key 无效或已被撤销）" : res.status === 404 ? "（模型名不存在或你的账号无权使用）" : res.status === 429 ? "（额度或频率超限）" : "";
    return new Error(`模型服务返回 ${res.status}${hint}${msg ? "：" + String(msg).slice(0, 160) : ""}`);
  },
  async complete(system, messages, maxTokens = 400) {
    const a = this.active();
    const res = await this._req(system, messages, maxTokens, false);
    if (!res.ok) throw await this._err(res);
    const d = await res.json();
    if (a.mode === "anthropic") return (d.content || []).filter((c) => c.type === "text").map((c) => c.text).join("");
    if (typeof d.output_text === "string") return d.output_text;
    return (d.output || []).flatMap((o) => o.content || []).map((c) => c.text || "").join("");
  },
  async stream(system, messages, onText, maxTokens = 1800) {
    const a = this.active();
    const res = await this._req(system, messages, maxTokens, true);
    if (!res.ok || !res.body) throw await this._err(res);
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n"); buf = lines.pop() || "";
      for (const line of lines) {
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload || payload === "[DONE]") continue;
        try {
          const ev = JSON.parse(payload);
          if (a.mode === "anthropic" && ev.type === "content_block_delta" && ev.delta?.type === "text_delta") onText(ev.delta.text);
          else if (a.mode === "openai" && ev.type === "response.output_text.delta") onText(ev.delta || "");
          else if (ev.type === "error") throw new Error(ev.error?.message || "模型服务出错");
        } catch (e) { if (e instanceof Error && e.message && !(e instanceof SyntaxError)) throw e; }
      }
    }
  },
  async test() {
    const a = this.active();
    if (!a) throw new Error("还没有可用的连接");
    const t = Date.now();
    if (this.isDirect()) {
      const out = await this.complete("Reply with exactly: OK", [{ role: "user", content: "ping" }], 20);
      return `${a.label} · ${a.model} 连接成功（${Date.now() - t} ms，回复：${out.trim().slice(0, 20)}）`;
    }
    return `${a.label} · ${a.model} 可用（由${a.mode === "cli" ? "本机订阅" : "网站服务器"}提供）`;
  },
};
Connect.load();

function updateConnBadge() {
  const el = document.getElementById("connBtn"); if (!el) return;
  const a = Connect.active();
  el.innerHTML = a ? `<span class="dot" style="background:#15803d;margin:0 6px 0 0"></span>${esc(a.label)}` : `<span class="dot" style="background:#b8bec8;margin:0 6px 0 0"></span>连接 Claude`;
  el.title = a ? `${a.label} · ${a.model}` : "选择 Claude 的连接方式";
}

async function openConnect() {
  await Connect.serverInfo();
  const local = await Connect.localStatus();
  const c = Connect.cfg; const api = Connect.apiOptions(); const cli = Connect.cliOptions();
  const mode = Connect.active()?.mode || c.mode || (local ? "cli" : api.length ? "server" : "anthropic");
  const opt = (list, sel) => list.map((m) => `<option ${m === sel ? "selected" : ""}>${esc(m)}</option>`).join("");
  const card = (id, title, desc, body, disabled) => `<label class="conn-opt ${disabled ? "off" : ""}"><input type="radio" name="cmode" value="${id}" ${mode === id ? "checked" : ""} ${disabled ? "disabled" : ""}>
    <div><b>${title}</b><div class="conn-desc">${desc}</div>${body ? `<div class="conn-body">${body}</div>` : ""}</div></label>`;
  const dlg = document.createElement("div");
  dlg.className = "modal-back"; dlg.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="connTitle">
    <div class="section-head"><h2 id="connTitle">连接 Claude</h2><button class="linkbtn" data-close aria-label="关闭">✕</button></div>
    <p class="conn-desc" style="margin-top:-4px">AI 问答、中文检索和研究助手会通过这里选择的方式调用 Claude。具体用哪个模型（Sonnet / Opus / Haiku）在 AI 问答页顶部切换。</p>
    ${card("server", "网站提供的模型", api.length ? `由网站服务器调用：${api.map((p) => `${esc(p.label)} · ${esc(p.model)}`).join("、")}` : "这个网站目前没有配置服务器端的模型。",
      api.length > 1 ? `<select id="cSrv" class="inp">${api.map((p) => `<option value="${p.id}" ${p.id === c.serverProvider ? "selected" : ""}>${esc(p.label)} · ${esc(p.model)}</option>`).join("")}</select>` : "", !api.length)}
    ${local ? card("cli", "用 Claude 账号（邮箱）登录", "使用你的 Claude Pro / Max 包月订阅，不需要 API key。点下面的按钮，电脑上会弹出一个窗口：没装 Claude Code 会先自动安装，然后选择用 Claude 账号登录，在打开的网页里输入邮箱和验证码即可。",
      `<div style="width:100%"><div id="cLocalState" class="conn-desc" style="margin-bottom:8px">${local.logged_in ? "✓ 已登录，可以直接使用" : local.installed ? "已安装 Claude Code，尚未确认登录" : "这台电脑还没有安装 Claude Code"}</div>
      <div class="row-actions"><button type="button" class="btn small primary" id="cLogin">${local.installed ? "用邮箱登录 Claude" : "安装并用邮箱登录"}</button><button type="button" class="btn small" id="cCheck">我已登录，检查状态</button></div></div>`, false) : ""}
    ${card("anthropic", "我的 Claude API key", `在 <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">Anthropic Console</a> 创建，以 sk-ant- 开头。`,
      `<input id="cAKey" class="inp" type="password" placeholder="sk-ant-..." value="${esc(c.anthropicKey)}" autocomplete="off">`, false)}
    <label class="conn-desc" style="display:flex;gap:6px;align-items:center;margin:10px 0"><input type="checkbox" id="cRemember" ${c.remember ? "checked" : ""}> 在这个浏览器里记住我的 key（不勾选则关闭标签页后清除）</label>
    <p class="conn-desc">你的 key 只保存在本浏览器，并且只直接发送给 Anthropic，不会发到本网站的服务器。在公共电脑上请不要勾选"记住"。</p>
    <div id="cStatus" class="conn-desc" style="min-height:20px"></div>
    <div class="row-actions" style="justify-content:flex-end"><button class="btn" id="cClear">清除我的 key</button><button class="btn" id="cTest">测试连接</button><button class="btn primary" id="cSave">保存</button></div></div>`;
  document.body.appendChild(dlg);
  const $ = (id) => dlg.querySelector("#" + id);
  const collect = () => {
    c.mode = dlg.querySelector("input[name=cmode]:checked")?.value || c.mode;
    c.anthropicKey = $("cAKey").value.trim();
    c.remember = $("cRemember").checked;
    if ($("cSrv")) c.serverProvider = $("cSrv").value; if ($("cCli")) c.cliProvider = $("cCli").value;
  };
  const close = () => { dlg.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  dlg.addEventListener("click", (e) => { if (e.target === dlg || e.target.closest("[data-close]")) close(); });
  dlg.querySelectorAll(".conn-body input, .conn-body select").forEach((el) => el.addEventListener("focus", () => { const r = el.closest(".conn-opt").querySelector("input[type=radio]"); if (!r.disabled) r.checked = true; }));
  const setLocal = (text, color) => { const el = $("cLocalState"); if (el) { el.textContent = text; el.style.color = color || ""; } };
  if ($("cLogin")) $("cLogin").onclick = async (e) => {
    e.preventDefault(); dlg.querySelector("input[value=cli]").checked = true;
    try {
      const r = await fetch("/api/claude/login", { method: "POST", headers: { "x-airg": "1", "content-type": "application/json" }, body: "{}" });
      const j = await r.json();
      if (j.manual) setLocal(j.manual);
      else setLocal(j.installing ? "已打开安装窗口：安装完成后会自动进入登录，选择 Claude 账号登录，在浏览器里输入邮箱和验证码。完成后点「我已登录，检查状态」。"
        : "已打开登录窗口：选择用 Claude 账号登录，在浏览器里输入邮箱和验证码。完成后回到这里点「我已登录，检查状态」。");
    } catch { setLocal("无法启动登录窗口，请确认网站是通过「启动网站.bat」打开的。", "#b42318"); }
  };
  if ($("cCheck")) $("cCheck").onclick = async (e) => {
    e.preventDefault(); dlg.querySelector("input[value=cli]").checked = true;
    setLocal("正在检查（会让 Claude 回复一句话，约 10–30 秒）…");
    const st = await Connect.localStatus(true);
    if (st?.logged_in) {
      Connect.server = null; await Connect.serverInfo();
      Object.assign(c, { mode: "cli", cliProvider: "claude-cli" }); Connect.save();
      setLocal("✓ 已登录，可以直接使用。点「保存」开始提问。", "var(--good)");
    } else setLocal(st?.installed ? "还没有登录成功。请在弹出的窗口里完成登录后再检查。" + (st?.detail ? `（${st.detail}）` : "") : "还没检测到 Claude Code，安装完成后再点检查。", "#b42318");
  };
  $("cTest").onclick = async () => {
    collect(); $("cStatus").textContent = "正在测试…"; $("cStatus").style.color = "";
    try { $("cStatus").textContent = "✓ " + await Connect.test(); $("cStatus").style.color = "var(--good)"; }
    catch (e) { $("cStatus").textContent = "✗ " + e.message; $("cStatus").style.color = "#b42318"; }
  };
  $("cSave").onclick = () => { collect(); Connect.save(); close(); toast(Connect.active() ? `已连接：${Connect.active().label}` : "已保存，但还没有可用的连接"); if (location.hash.startsWith("#/ask")) route(); };
  $("cClear").onclick = () => { $("cAKey").value = ""; collect(); c.openaiKey = ""; if (c.mode === "anthropic") c.mode = ""; Connect.save(); $("cStatus").textContent = "已清除保存的 key"; };
  setTimeout(() => (dlg.querySelector("input[name=cmode]:checked") || dlg.querySelector("input"))?.focus(), 0);
}

document.addEventListener("DOMContentLoaded", async () => {
  const btn = document.getElementById("connBtn");
  if (btn) btn.onclick = openConnect;
  await Connect.serverInfo(); updateConnBadge();
});
