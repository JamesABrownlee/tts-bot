import asyncio
import contextlib
import json
import os
import time
from typing import Any, Dict, Optional

import discord
from aiohttp import web
from discord.ext import commands

from utils.config import ALL_VOICES, FALLBACK_VOICE, VOICE_ID_TO_NAME
from utils.logger import get_logger
from utils.settings_store import VERSION
from utils.tts_pipeline import get_tts_stream

logger = get_logger("webui")


def _truthy(value: Optional[str], default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_bearer_token(request: web.Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return request.query.get("token")


def _sse_encode(data: str) -> bytes:
    # SSE frames require each line to be prefixed with `data:`.
    lines = data.splitlines() or [""]
    payload = "".join([f"data: {line}\n" for line in lines])
    payload += "\n"
    return payload.encode("utf-8")


def _layout(title: str, body_html: str, *, token_required: bool) -> str:
    token_banner = (
        "<div class=\"pill warn\">API token required</div>" if token_required else "<div class=\"pill ok\">No API token</div>"
    )

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
  <title>{title}</title>
  <style>
    :root {{
      --bg0: #0b1020;
      --bg1: #101a33;
      --card: rgba(255,255,255,0.06);
      --card2: rgba(255,255,255,0.09);
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.68);
      --brand: #45d0ff;
      --brand2: #7bffb2;
      --danger: #ff5d6c;
      --warn: #ffcc66;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, \"Liberation Mono\", \"Courier New\", monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, \"Apple Color Emoji\", \"Segoe UI Emoji\";
    }}

    body {{
      margin: 0;
      font-family: var(--sans);
      color: var(--text);
      background:
        radial-gradient(1200px 600px at 10% 10%, rgba(69,208,255,0.15), transparent 60%),
        radial-gradient(900px 500px at 90% 20%, rgba(123,255,178,0.10), transparent 55%),
        radial-gradient(800px 800px at 50% 100%, rgba(255,93,108,0.10), transparent 55%),
        linear-gradient(160deg, var(--bg0), var(--bg1));
      min-height: 100vh;
    }}

    .wrap {{ max-width: 980px; margin: 0 auto; padding: 28px 18px 56px; }}

    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 18px;
    }}

    .brand {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}

    .brand h1 {{
      margin: 0;
      font-size: 20px;
      letter-spacing: 0.2px;
    }}

    .brand small {{ color: var(--muted); }}

    nav {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }}

    a.btn, button.btn {{
      appearance: none;
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.04);
      color: var(--text);
      padding: 10px 12px;
      border-radius: 12px;
      text-decoration: none;
      cursor: pointer;
      transition: transform 120ms ease, background 120ms ease, border 120ms ease;
    }}

    a.btn:hover, button.btn:hover {{
      background: rgba(255,255,255,0.07);
      border-color: rgba(255,255,255,0.18);
      transform: translateY(-1px);
    }}

    .pill {{
      font-size: 12px;
      border-radius: 999px;
      padding: 6px 10px;
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.04);
      color: var(--muted);
    }}

    .pill.ok {{ border-color: rgba(123,255,178,0.22); color: rgba(123,255,178,0.85); background: rgba(123,255,178,0.08); }}
    .pill.warn {{ border-color: rgba(255,204,102,0.22); color: rgba(255,204,102,0.90); background: rgba(255,204,102,0.08); }}

    .card {{
      background: var(--card);
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 18px;
      padding: 16px;
      box-shadow: 0 18px 55px rgba(0,0,0,0.28);
    }}

    .grid {{ display: grid; grid-template-columns: 1fr; gap: 14px; }}
    @media (min-width: 860px) {{ .grid.two {{ grid-template-columns: 1.1fr 0.9fr; }} }}

    .kv {{ display: grid; grid-template-columns: 180px 1fr; gap: 8px 12px; }}
    .kv div {{ color: var(--muted); }}
    .kv code {{ font-family: var(--mono); color: var(--text); }}

    .inputrow {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    input[type=text], input[type=number], select {{
      background: rgba(0,0,0,0.28);
      color: var(--text);
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 12px;
      padding: 10px 12px;
      min-width: 240px;
      outline: none;
    }}

    textarea {{
      width: 100%;
      box-sizing: border-box;
      background: rgba(0,0,0,0.28);
      color: var(--text);
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 12px;
      padding: 10px 12px;
      outline: none;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.45;
      min-height: 220px;
      resize: vertical;
    }}

    textarea:focus {{ border-color: rgba(69,208,255,0.35); box-shadow: 0 0 0 3px rgba(69,208,255,0.12); }}

    input[type=text]:focus, input[type=number]:focus, select:focus {{ border-color: rgba(69,208,255,0.35); box-shadow: 0 0 0 3px rgba(69,208,255,0.12); }}

    pre.log {{
      margin: 0;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.45;
      color: rgba(255,255,255,0.88);
      background: rgba(0,0,0,0.28);
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 14px;
      padding: 12px;
      overflow: auto;
      max-height: 70vh;
      white-space: pre-wrap;
      word-break: break-word;
    }}

    .danger {{ color: var(--danger); }}
    .muted {{ color: var(--muted); }}

    .btn.small {{ padding: 6px 10px; border-radius: 10px; font-size: 12px; }}

    .voice-list {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      max-height: 52vh;
      overflow: auto;
      padding: 10px;
      background: rgba(0,0,0,0.18);
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 14px;
    }}

    .voice-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 8px 10px;
      border-radius: 12px;
      background: rgba(255,255,255,0.03);
      border: 1px solid rgba(255,255,255,0.06);
    }}

    .voice-row:hover {{ background: rgba(255,255,255,0.05); border-color: rgba(255,255,255,0.10); }}
    .voice-meta {{ display: flex; align-items: center; gap: 10px; min-width: 0; }}
    .voice-meta input[type=checkbox] {{ transform: translateY(1px); }}
    .voice-name {{ font-size: 13px; color: rgba(255,255,255,0.92); }}
    .voice-id {{ font-family: var(--mono); font-size: 12px; color: var(--muted); }}
  </style>
  <script>
    window.__TOKEN_REQUIRED__ = {str(token_required).lower()};

    function getToken() {{
      return localStorage.getItem('web_token') || '';
    }}

    function setToken(v) {{
      localStorage.setItem('web_token', v || '');
    }}

    function authHeaders() {{
      const t = getToken();
      if (!t) return {{}};
      return {{ 'Authorization': 'Bearer ' + t }};
    }}

    async function apiFetch(url, opts) {{
      const options = opts || {{}};
      options.headers = Object.assign({{}}, options.headers || {{}}, authHeaders());
      const res = await fetch(url, options);
      const ct = res.headers.get('content-type') || '';
      if (!res.ok) {{
        let msg = res.status + ' ' + res.statusText;
        try {{
          if (ct.includes('application/json')) {{
            const j = await res.json();
            if (j && j.error) msg = j.error;
          }} else {{
            msg = await res.text();
          }}
        }} catch (e) {{}}
        throw new Error(msg);
      }}
      if (ct.includes('application/json')) return await res.json();
      return await res.text();
    }}
  </script>
</head>
<body>
  <div class=\"wrap\">
    <header>
      <div class=\"brand\">
        <h1>Discord TTS Bot Control Panel</h1>
        <small class=\"muted\">A tiny web UI served by aiohttp</small>
      </div>
      <nav>
        <a class=\"btn\" href=\"/\">Home</a>
        <a class=\"btn\" href=\"/logs\">Logs</a>
        <a class=\"btn\" href=\"/settings\">Settings</a>
        {token_banner}
      </nav>
    </header>

    {body_html}
  </div>
</body>
</html>"""


def _index_body() -> str:
    return """
<div class="grid two">
  <div class="card">
    <h2 style="margin:0 0 10px 0;">Status</h2>
    <div class="kv" id="statusKv">
      <div>Bot</div><code>loading…</code>
      <div>Guilds</div><code>loading…</code>
      <div>Uptime</div><code>loading…</code>
      <div>Web</div><code>loading…</code>
    </div>
  </div>

  <div class="card">
    <h2 style="margin:0 0 10px 0;">API Token</h2>
    <p class="muted" style="margin:0 0 10px 0;">
      If this server is configured with <code>WEB_UI_TOKEN</code>, enter it here to access the API. It is stored in your browser's localStorage.
    </p>
    <div class="inputrow">
      <input id="tokenInput" type="text" placeholder="WEB_UI_TOKEN (optional)" />
      <button class="btn" id="saveToken">Save</button>
      <button class="btn" id="clearToken">Clear</button>
    </div>
    <div class="inputrow" style="margin-top:10px;">
      <span class="pill" id="tokenStatePill">checking…</span>
      <span class="muted" id="tokenMsg"></span>
    </div>
    <p class="muted" style="margin:10px 0 0 0;">
      Tip: When token is set, the logs page uses it in the URL for EventSource.
    </p>
  </div>
</div>

<div class="card" style="margin-top:14px;">
  <h2 style="margin:0 0 10px 0;">Quick Actions</h2>
  <div class="inputrow">
    <a class="btn" href="/logs">View Logs</a>
    <a class="btn" href="/settings">Edit Settings</a>
  </div>
</div>

<script>
  const tokenInput = document.getElementById('tokenInput');
  const tokenMsg = document.getElementById('tokenMsg');
  const tokenStatePill = document.getElementById('tokenStatePill');

  function setPill(el, ok, text) {
    el.textContent = text;
    el.classList.remove('ok', 'warn');
    el.classList.add(ok ? 'ok' : 'warn');
  }

  function getStoredToken() {
    return (localStorage.getItem('web_token') || '').trim();
  }

  function updateTokenUi() {
    const t = getStoredToken();
    tokenInput.value = t;

    if (window.__TOKEN_REQUIRED__) {
      setPill(tokenStatePill, !!t, t ? 'Token saved' : 'Token missing');
    } else {
      setPill(tokenStatePill, true, t ? 'Token saved (not required)' : 'No token (not required)');
    }
  }

  updateTokenUi();

  document.getElementById('saveToken').addEventListener('click', () => {
    const v = (tokenInput.value || '').trim();
    localStorage.setItem('web_token', v);
    tokenMsg.textContent = v ? 'Saved.' : 'Cleared.';
    updateTokenUi();
  });

  document.getElementById('clearToken').addEventListener('click', () => {
    localStorage.removeItem('web_token');
    tokenInput.value = '';
    tokenMsg.textContent = 'Cleared.';
    updateTokenUi();
  });

  tokenInput.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    document.getElementById('saveToken').click();
  });

  function fmtUptime(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    const d = Math.floor(seconds / 86400); seconds -= d * 86400;
    const h = Math.floor(seconds / 3600); seconds -= h * 3600;
    const m = Math.floor(seconds / 60); seconds -= m * 60;
    const parts = [];
    if (d) parts.push(d + 'd');
    if (h || parts.length) parts.push(h + 'h');
    if (m || parts.length) parts.push(m + 'm');
    parts.push(seconds + 's');
    return parts.join(' ');
  }

  (async () => {
    const kv = document.getElementById('statusKv');
    try {
      const s = await apiFetch('/api/status');
      kv.innerHTML = `
        <div>Bot</div><code>${(s.user || 'not connected')}</code>
        <div>Version</div><code>${(s.tts_version || 'unknown')}</code>
        <div>discord.py</div><code>${(s.discord_py_version || 'unknown')}</code>
        <div>Guilds</div><code>${s.guild_count}</code>
        <div>Uptime</div><code>${fmtUptime(s.uptime_seconds)}</code>
        <div>Web</div><code>${s.web_host}:${s.web_port}</code>
      `;
    } catch (e) {
      kv.innerHTML = `<div class="danger">Error</div><code class="danger">${e.message}</code>`;
    }
  })();
</script>
"""


def _logs_body(token_required: bool) -> str:
    token_hint = (
        "<span class=\"pill warn\" id=\"tokenStreamPill\">Token required</span>"
        if token_required
        else "<span class=\"pill ok\" id=\"tokenStreamPill\">No token required</span>"
    )

    html = """
<div class="card">
  <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">
    <h2 style="margin:0;">Logs</h2>
    <div class="inputrow">
      __TOKEN_HINT__
      <button class="btn" id="pauseBtn">Pause</button>
      <button class="btn" id="clearBtn">Clear View</button>
    </div>
  </div>
  <p class="muted" style="margin:10px 0 12px 0;">Streaming from <code>/api/logs/stream</code>.</p>
  <pre class="log" id="logBox">Connecting…</pre>
</div>

<script>
  const logBox = document.getElementById('logBox');
  const pauseBtn = document.getElementById('pauseBtn');
  const clearBtn = document.getElementById('clearBtn');
  const tokenStreamPill = document.getElementById('tokenStreamPill');

  let paused = false;
  let es = null;
  let streamTail = 0;

  function setTokenPill(ok, text) {
    if (!tokenStreamPill) return;
    tokenStreamPill.textContent = text;
    tokenStreamPill.classList.remove('ok', 'warn');
    tokenStreamPill.classList.add(ok ? 'ok' : 'warn');
  }

  function appendLine(text) {
    if (!text) return;
    logBox.textContent += (logBox.textContent.endsWith('\\n') || logBox.textContent.length === 0) ? text + '\\n' : '\\n' + text + '\\n';
    logBox.scrollTop = logBox.scrollHeight;
  }

  function connect() {
    const t = getToken();
    if (window.__TOKEN_REQUIRED__ && !t) {
      setTokenPill(false, 'Token missing');
      logBox.textContent = '[webui] Token required. Open Home, set WEB_UI_TOKEN, then refresh this page.\\n';
      return;
    }
    setTokenPill(true, window.__TOKEN_REQUIRED__ ? 'Token set' : 'No token required');

    const qs = new URLSearchParams();
    qs.set('tail', String(streamTail));
    if (window.__TOKEN_REQUIRED__) qs.set('token', t);
    const url = '/api/logs/stream?' + qs.toString();

    if (es) es.close();
    es = new EventSource(url);

    es.onmessage = (ev) => {
      if (paused) return;
      streamTail = 0;
      appendLine(ev.data);
    };

    es.onerror = () => {
      if (es) es.close();
      es = null;
      if (!paused) {
        appendLine('[webui] disconnected — retrying in 2s');
        setTimeout(connect, 2000);
      }
    };
  }

  pauseBtn.addEventListener('click', () => {
    paused = !paused;
    pauseBtn.textContent = paused ? 'Resume' : 'Pause';
    if (paused) {
      if (es) es.close();
      es = null;
    } else {
      connect();
    }
  });

  clearBtn.addEventListener('click', () => {
    logBox.textContent = '';
  });

  (async () => {
    const t = getToken();
    if (window.__TOKEN_REQUIRED__ && !t) {
      setTokenPill(false, 'Token missing');
      logBox.textContent = '[webui] Token required. Open Home, set WEB_UI_TOKEN, then refresh this page.\\n';
      return;
    }
    setTokenPill(true, window.__TOKEN_REQUIRED__ ? 'Token set' : 'No token required');

    try {
      const res = await apiFetch('/api/logs?tail=500');
      const lines = (res && Array.isArray(res.lines)) ? res.lines : [];
      logBox.textContent = lines.length ? (lines.join('\\n') + '\\n') : '';
      logBox.scrollTop = logBox.scrollHeight;
      streamTail = 0;
    } catch (e) {
      logBox.textContent = '[webui] Failed to load initial logs: ' + (e && e.message ? e.message : String(e)) + '\\n';
      streamTail = 500;
    }

    connect();
  })();
</script>
"""

    return html.replace("__TOKEN_HINT__", token_hint)


def _settings_body() -> str:
    return """
<div class="card">
  <h2 style="margin:0 0 10px 0;">Settings</h2>
  <p class="muted" style="margin:0 0 14px 0;">Edits are saved per server to SQLite (<code>guild_settings</code> table).</p>

  <div class="inputrow" style="margin:0 0 14px 0;">
    <label class="muted" for="guildSelect">Server</label>
    <select id="guildSelect"></select>
    <span class="pill" id="guildPill">loading…</span>
  </div>

  <div class="grid" style="gap:10px;">
    <label class="muted">Max TTS Characters</label>
    <input id="maxChars" type="number" min="1" max="2000" />

    <label class="muted">Fallback Voice ID</label>
    <select id="fallbackVoice"></select>

	    <label class="muted">Default Voice ID (server default)</label>
	    <select id="defaultVoice"></select>

    <label class="muted">Auto Speak Voice Chat Messages</label>
    <div class="inputrow">
      <button class="btn" id="toggleAutoRead">Toggle</button>
      <span class="pill" id="autoReadPill">loading…</span>
    </div>

	    <label class="muted">Leave When Alone In Voice Channel</label>
	    <div class="inputrow">
	      <button class="btn" id="toggleLeave">Toggle</button>
	      <span class="pill" id="leavePill">loading…</span>
	    </div>

	    <label class="muted">Greet Members On Join</label>
	    <div class="inputrow">
	      <button class="btn" id="toggleGreetJoin">Toggle</button>
	      <span class="pill" id="greetJoinPill">loading…</span>
	    </div>

	    <label class="muted">Say Goodbye On Leave</label>
	    <div class="inputrow">
	      <button class="btn" id="toggleFarewellLeave">Toggle</button>
	      <span class="pill" id="farewellLeavePill">loading…</span>
	    </div>

	    <div class="inputrow" style="margin-top:8px;">
	      <button class="btn" id="saveBtn">Save Settings</button>
	      <span class="muted" id="saveMsg"></span>
	    </div>
  </div>

  <div style="height:1px; background: rgba(255,255,255,0.10); margin:14px 0;"></div>

  <h3 style="margin:0 0 10px 0;">Voice Restriction</h3>
  <p class="muted" style="margin:0 0 12px 0;">
    When enabled, users can only pick voices you allow for this server (affects Discord menus and autocomplete).
    Default + fallback voices are always forced into the allowlist while restriction is enabled.
  </p>

  <div class="inputrow" style="margin:0 0 10px 0;">
    <button class="btn" id="toggleRestrict">Toggle</button>
    <span class="pill" id="restrictPill">loading…</span>
    <span class="pill" id="voiceCountPill">0 selected</span>
  </div>

	  <div id="voiceRestrictBox" style="display:none;">
	    <div class="inputrow" style="margin:0 0 10px 0;">
	      <input id="voiceSearch" type="text" placeholder="Search voices…" />
	      <input id="previewText" type="text" placeholder="Preview text (optional)" />
	      <button class="btn small" id="selectAllVoices">All</button>
	      <button class="btn small" id="selectNoneVoices">None</button>
	    </div>
	    <div class="voice-list" id="voiceList"><span class="muted">Loading voices…</span></div>
	    <audio id="voicePlayer" style="width:100%; margin-top:10px; display:none;" controls></audio>
	    <p class="muted" style="margin:10px 0 0 0;">
	      Tip: Click Play to audition a voice (streamed from <code>/api/voices/preview</code>).
	    </p>
	  </div>

	  <div style="height:1px; background: rgba(255,255,255,0.10); margin:14px 0;"></div>

	  <h3 style="margin:0 0 10px 0;">Advanced (JSON)</h3>
	  <p class="muted" style="margin:0 0 12px 0;">
	    Edit the raw settings JSON for this server (useful if new settings are added later).
	  </p>
	  <textarea id="settingsJson" spellcheck="false"></textarea>
	  <div class="inputrow" style="margin-top:10px;">
	    <button class="btn small" id="refreshJsonBtn">Refresh JSON</button>
	    <button class="btn small" id="applyJsonBtn">Apply JSON</button>
	    <span class="muted" id="jsonMsg"></span>
	  </div>
	</div>

	<script>
	  const guildSelect = document.getElementById('guildSelect');
  const guildPill = document.getElementById('guildPill');
  const elMaxChars = document.getElementById('maxChars');
  const elFallbackVoice = document.getElementById('fallbackVoice');
  const elDefaultVoice = document.getElementById('defaultVoice');
  const saveMsg = document.getElementById('saveMsg');
  const restrictPill = document.getElementById('restrictPill');
  const voiceCountPill = document.getElementById('voiceCountPill');
  const voiceRestrictBox = document.getElementById('voiceRestrictBox');
  const voiceList = document.getElementById('voiceList');
	  const voiceSearch = document.getElementById('voiceSearch');
	  const previewText = document.getElementById('previewText');
	  const voicePlayer = document.getElementById('voicePlayer');
	  const settingsJson = document.getElementById('settingsJson');
	  const jsonMsg = document.getElementById('jsonMsg');

  let current = null;
  let allVoices = [];
  let allowedSet = new Set();

  function pill(el, ok, text) {
    el.textContent = text;
    el.classList.remove('ok', 'warn');
    el.classList.add(ok ? 'ok' : 'warn');
  }

  function selectedGuildId() {
    return (guildSelect.value || '').trim();
  }

  async function loadGuilds() {
    const res = await apiFetch('/api/guilds');
    const guilds = (res && Array.isArray(res.guilds)) ? res.guilds : [];

    guildSelect.textContent = '';
    if (!guilds.length) {
      pill(guildPill, false, 'No servers');
      return;
    }

    for (const g of guilds) {
      const opt = document.createElement('option');
      opt.value = g.id;
      opt.textContent = `${g.name} (${g.id})`;
      guildSelect.appendChild(opt);
    }

    const saved = (localStorage.getItem('web_guild_id') || '').trim();
    const ok = saved && guilds.some(g => g.id === saved);
    guildSelect.value = ok ? saved : guilds[0].id;
    localStorage.setItem('web_guild_id', selectedGuildId());
    pill(guildPill, true, `${guilds.length} servers`);
  }

	  async function loadSettings() {
	    const gid = selectedGuildId();
	    if (!gid) return;
	    current = await apiFetch('/api/settings?guild_id=' + encodeURIComponent(gid));
	    applyCurrentToForm();
	    refreshJsonFromCurrent();
	  }

	  async function loadVoices() {
	    const res = await apiFetch('/api/voices');
	    const voices = (res && Array.isArray(res.voices)) ? res.voices : [];
	    allVoices = voices.map(v => ({ id: String(v.id), name: String(v.name || v.id) }));
	    renderVoiceSelects();
	  }

	  function renderVoiceSelects() {
	    if (!elFallbackVoice || !elDefaultVoice) return;
	    const curFallback = (elFallbackVoice.value || '').trim();
	    const curDefault = (elDefaultVoice.value || '').trim();

	    const buildOptions = () => {
	      const frag = document.createDocumentFragment();
	      for (const v of allVoices) {
	        const opt = document.createElement('option');
	        opt.value = v.id;
	        opt.textContent = v.name ? `${v.name} (${v.id})` : v.id;
	        frag.appendChild(opt);
	      }
	      return frag;
	    };

	    elFallbackVoice.innerHTML = '';
	    elDefaultVoice.innerHTML = '';
	    elFallbackVoice.appendChild(buildOptions());
	    elDefaultVoice.appendChild(buildOptions());

	    if (curFallback) elFallbackVoice.value = curFallback;
	    if (curDefault) elDefaultVoice.value = curDefault;
	  }

	  function ensureSelectOption(selectEl, value) {
	    if (!selectEl || !value) return;
	    const exists = Array.from(selectEl.options).some(opt => opt.value === value);
	    if (exists) return;
	    const opt = document.createElement('option');
	    opt.value = value;
	    opt.textContent = value;
	    selectEl.appendChild(opt);
	  }

	  function applyCurrentToForm() {
	    if (!current) return;
	    const max = parseInt(current.max_tts_chars, 10);
	    elMaxChars.value = Number.isFinite(max) ? String(max) : '300';
	    const fallbackValue = String(current.fallback_voice || '').trim();
	    const defaultValue = String(current.default_voice_id || '').trim();
	    ensureSelectOption(elFallbackVoice, fallbackValue);
	    ensureSelectOption(elDefaultVoice, defaultValue);
	    elFallbackVoice.value = fallbackValue;
	    elDefaultVoice.value = defaultValue;

		    if (current.auto_read_messages === undefined) current.auto_read_messages = true;
		    if (current.leave_when_alone === undefined) current.leave_when_alone = true;
		    if (current.greet_on_join === undefined) current.greet_on_join = false;
		    if (current.farewell_on_leave === undefined) current.farewell_on_leave = false;
		    current.auto_read_messages = !!current.auto_read_messages;
		    current.leave_when_alone = !!current.leave_when_alone;
		    current.greet_on_join = !!current.greet_on_join;
		    current.farewell_on_leave = !!current.farewell_on_leave;
		    current.restrict_voices = !!current.restrict_voices;

		    pill(document.getElementById('autoReadPill'), current.auto_read_messages, current.auto_read_messages ? 'enabled' : 'disabled');
		    pill(document.getElementById('leavePill'), current.leave_when_alone, current.leave_when_alone ? 'enabled' : 'disabled');
		    pill(document.getElementById('greetJoinPill'), current.greet_on_join, current.greet_on_join ? 'enabled' : 'disabled');
		    pill(document.getElementById('farewellLeavePill'), current.farewell_on_leave, current.farewell_on_leave ? 'enabled' : 'disabled');

	    allowedSet = new Set(Array.isArray(current.allowed_voice_ids) ? current.allowed_voice_ids.map(String) : []);
	    updateRestrictUi();
	  }

  function requiredVoiceIds() {
    const req = [];
    const fv = (elFallbackVoice.value || '').trim();
    const dv = (elDefaultVoice.value || '').trim();
    if (fv) req.push(fv);
    if (dv && dv !== fv) req.push(dv);
    return req;
  }

  function syncRequiredVoices() {
    for (const vid of requiredVoiceIds()) {
      allowedSet.add(vid);
    }
  }

  function updateVoiceCount() {
    if (current && !current.restrict_voices) {
      pill(voiceCountPill, true, 'not restricted');
      return;
    }
    const n = allowedSet.size;
    pill(voiceCountPill, n > 0, n + ' selected');
  }

  function buildPreviewUrl(voiceId) {
    const qs = new URLSearchParams();
    qs.set('voice_id', voiceId);
    const t = (previewText.value || '').trim();
    if (t) qs.set('text', t);
    if (window.__TOKEN_REQUIRED__) {
      const tok = (getToken() || '').trim();
      if (tok) qs.set('token', tok);
    }
    qs.set('ts', String(Date.now())); // cache buster
    return '/api/voices/preview?' + qs.toString();
  }

  function renderVoiceList() {
    voiceList.textContent = '';
    if (!allVoices.length) {
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.textContent = 'No voices loaded.';
      voiceList.appendChild(empty);
      updateVoiceCount();
      return;
    }

    syncRequiredVoices();
    const required = new Set(requiredVoiceIds());
    const q = (voiceSearch.value || '').trim().toLowerCase();
    let shown = 0;

    for (const v of allVoices) {
      const hay = (v.name + ' ' + v.id).toLowerCase();
      if (q && !hay.includes(q)) continue;
      shown++;

      const row = document.createElement('div');
      row.className = 'voice-row';

      const meta = document.createElement('div');
      meta.className = 'voice-meta';

      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.dataset.vid = v.id;
      cb.checked = allowedSet.has(v.id);
      if (required.has(v.id)) {
        cb.checked = true;
        cb.disabled = true;
        allowedSet.add(v.id);
      }

      const textWrap = document.createElement('div');
      textWrap.style.minWidth = '0';

      const nameDiv = document.createElement('div');
      nameDiv.className = 'voice-name';
      nameDiv.textContent = v.name || v.id;

      const idDiv = document.createElement('div');
      idDiv.className = 'voice-id';
      idDiv.textContent = v.id;

      textWrap.appendChild(nameDiv);
      textWrap.appendChild(idDiv);

      meta.appendChild(cb);
      meta.appendChild(textWrap);

      const actions = document.createElement('div');
      actions.className = 'inputrow';
      actions.style.gap = '8px';

      const playBtn = document.createElement('button');
      playBtn.className = 'btn small';
      playBtn.type = 'button';
      playBtn.dataset.play = v.id;
      playBtn.textContent = 'Play';

      actions.appendChild(playBtn);

      row.appendChild(meta);
      row.appendChild(actions);

      voiceList.appendChild(row);
    }

    if (!shown) {
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.textContent = 'No voices match your search.';
      voiceList.appendChild(empty);
    }

    updateVoiceCount();
  }

	  function updateRestrictUi() {
	    if (!current) return;
	    pill(restrictPill, !!current.restrict_voices, current.restrict_voices ? 'enabled' : 'disabled');
	    voiceRestrictBox.style.display = current.restrict_voices ? 'block' : 'none';
	    if (current.restrict_voices) renderVoiceList();
	    updateVoiceCount();
	  }

	  function orderedAllowedVoiceIds() {
	    syncRequiredVoices();
	    const ordered = [];
	    const seen = new Set();

	    if (Array.isArray(allVoices) && allVoices.length) {
	      for (const v of allVoices) {
	        if (allowedSet.has(v.id) && !seen.has(v.id)) {
	          ordered.push(v.id);
	          seen.add(v.id);
	        }
	      }
	    }

	    for (const vid of allowedSet) {
	      if (!seen.has(vid)) {
	        ordered.push(vid);
	        seen.add(vid);
	      }
	    }

	    return ordered;
	  }

	  function buildPayloadFromForm() {
	    const payload = Object.assign({}, current || {});

	    const max = parseInt(elMaxChars.value || '300', 10);
	    payload.max_tts_chars = Number.isFinite(max) ? max : 300;
		    payload.fallback_voice = (elFallbackVoice.value || '').trim();
		    payload.default_voice_id = (elDefaultVoice.value || '').trim();
		    payload.auto_read_messages = !!(current && current.auto_read_messages);
		    payload.leave_when_alone = !!(current && current.leave_when_alone);
		    payload.greet_on_join = !!(current && current.greet_on_join);
		    payload.farewell_on_leave = !!(current && current.farewell_on_leave);
		    payload.restrict_voices = !!(current && current.restrict_voices);
		    payload.allowed_voice_ids = orderedAllowedVoiceIds();

	    return payload;
	  }

	  function refreshJsonFromCurrent() {
	    if (!settingsJson) return;
	    settingsJson.value = JSON.stringify(current || {}, null, 2);
	  }

	  function refreshJsonFromForm() {
	    if (!settingsJson) return;
	    settingsJson.value = JSON.stringify(buildPayloadFromForm(), null, 2);
	  }

	  guildSelect.addEventListener('change', () => {
	    localStorage.setItem('web_guild_id', selectedGuildId());
	    loadSettings().catch(e => {
	      saveMsg.textContent = 'Error: ' + e.message;
      saveMsg.className = 'danger';
    });
  });

  document.getElementById('toggleAutoRead').addEventListener('click', () => {
    if (!current) return;
    current.auto_read_messages = !current.auto_read_messages;
    pill(document.getElementById('autoReadPill'), current.auto_read_messages, current.auto_read_messages ? 'enabled' : 'disabled');
  });

	  document.getElementById('toggleLeave').addEventListener('click', () => {
	    if (!current) return;
	    current.leave_when_alone = !current.leave_when_alone;
	    pill(document.getElementById('leavePill'), current.leave_when_alone, current.leave_when_alone ? 'enabled' : 'disabled');
	  });

	  document.getElementById('toggleGreetJoin').addEventListener('click', () => {
	    if (!current) return;
	    current.greet_on_join = !current.greet_on_join;
	    pill(document.getElementById('greetJoinPill'), current.greet_on_join, current.greet_on_join ? 'enabled' : 'disabled');
	  });

	  document.getElementById('toggleFarewellLeave').addEventListener('click', () => {
	    if (!current) return;
	    current.farewell_on_leave = !current.farewell_on_leave;
	    pill(document.getElementById('farewellLeavePill'), current.farewell_on_leave, current.farewell_on_leave ? 'enabled' : 'disabled');
	  });

  document.getElementById('toggleRestrict').addEventListener('click', () => {
    if (!current) return;
    current.restrict_voices = !current.restrict_voices;
    if (current.restrict_voices) syncRequiredVoices();
    updateRestrictUi();
  });

  document.getElementById('selectAllVoices').addEventListener('click', () => {
    allowedSet = new Set(allVoices.map(v => v.id));
    syncRequiredVoices();
    renderVoiceList();
  });

  document.getElementById('selectNoneVoices').addEventListener('click', () => {
    allowedSet = new Set();
    syncRequiredVoices();
    renderVoiceList();
  });

  voiceSearch.addEventListener('input', () => {
    if (!current || !current.restrict_voices) return;
    renderVoiceList();
  });

  voiceList.addEventListener('change', (ev) => {
    const t = ev.target;
    if (!t || t.tagName !== 'INPUT' || t.type !== 'checkbox') return;
    const vid = (t.dataset && t.dataset.vid) ? t.dataset.vid : '';
    if (!vid) return;
    if (t.checked) allowedSet.add(vid);
    else allowedSet.delete(vid);
    updateVoiceCount();
  });

  voiceList.addEventListener('click', (ev) => {
    const btn = ev.target && ev.target.closest ? ev.target.closest('button[data-play]') : null;
    if (!btn) return;

    if (window.__TOKEN_REQUIRED__ && !(getToken() || '').trim()) {
      saveMsg.textContent = 'Token required for preview. Set WEB_UI_TOKEN on Home.';
      saveMsg.className = 'danger';
      return;
    }

    const vid = btn.dataset.play;
    if (!vid) return;
    voicePlayer.style.display = 'block';
    voicePlayer.src = buildPreviewUrl(vid);
    voicePlayer.play().catch(() => {});
  });

  elFallbackVoice.addEventListener('change', () => {
    if (!current || !current.restrict_voices) return;
    syncRequiredVoices();
    renderVoiceList();
  });

	  elDefaultVoice.addEventListener('change', () => {
	    if (!current || !current.restrict_voices) return;
	    syncRequiredVoices();
	    renderVoiceList();
	  });

	  document.getElementById('refreshJsonBtn').addEventListener('click', () => {
	    if (!current) return;
	    jsonMsg.textContent = '';
	    try {
	      refreshJsonFromForm();
	      jsonMsg.textContent = 'Refreshed.';
	      jsonMsg.className = 'muted';
	    } catch (e) {
	      jsonMsg.textContent = 'Error: ' + e.message;
	      jsonMsg.className = 'danger';
	    }
	  });

	  document.getElementById('applyJsonBtn').addEventListener('click', () => {
	    if (!current) return;
	    jsonMsg.textContent = '';
	    try {
	      const raw = (settingsJson.value || '').trim();
	      const obj = raw ? JSON.parse(raw) : {};
	      if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error('JSON must be an object');
	      current = obj;
	      applyCurrentToForm();
	      jsonMsg.textContent = 'Applied.';
	      jsonMsg.className = 'muted';
	    } catch (e) {
	      jsonMsg.textContent = 'Error: ' + e.message;
	      jsonMsg.className = 'danger';
	    }
	  });

	  document.getElementById('saveBtn').addEventListener('click', async () => {
	    saveMsg.textContent = '';
	    try {
	      const gid = selectedGuildId();
	      if (!gid) throw new Error('No server selected');
	      const payload = buildPayloadFromForm();

	      current = await apiFetch('/api/settings?guild_id=' + encodeURIComponent(gid), {
	        method: 'POST',
	        headers: { 'Content-Type': 'application/json' },
	        body: JSON.stringify(payload),
	      });
	      allowedSet = new Set(Array.isArray(current.allowed_voice_ids) ? current.allowed_voice_ids.map(String) : []);
	      updateRestrictUi();
	      refreshJsonFromCurrent();
	      saveMsg.textContent = 'Saved.';
	      saveMsg.className = 'muted';
	    } catch (e) {
	      saveMsg.textContent = 'Error: ' + e.message;
      saveMsg.className = 'danger';
    }
  });

  (async () => {
    try {
      await loadGuilds();
      await loadVoices();
      await loadSettings();
    } catch (e) {
      saveMsg.textContent = 'Error: ' + e.message;
      saveMsg.className = 'danger';
    }
  })();
</script>
"""


class WebUICog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.enabled = _truthy(os.getenv("WEB_UI_ENABLED"), default=True)
        self.host = os.getenv("WEB_HOST") or "127.0.0.1"
        self.port = int(os.getenv("WEB_PORT") or "8080")
        self.token = (os.getenv("WEB_UI_TOKEN") or "").strip() or None

        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        self._app = web.Application(middlewares=[self._auth_middleware])
        self._app.router.add_get("/", self.page_index)
        self._app.router.add_get("/logs", self.page_logs)
        self._app.router.add_get("/settings", self.page_settings)

        self._app.router.add_get("/api/status", self.api_status)
        self._app.router.add_get("/api/guilds", self.api_guilds)
        self._app.router.add_get("/api/voices", self.api_voices)
        self._app.router.add_get("/api/voices/preview", self.api_voice_preview)
        self._app.router.add_get("/api/logs", self.api_logs)
        self._app.router.add_get("/api/logs/stream", self.api_logs_stream)
        self._app.router.add_get("/api/settings", self.api_settings_get)
        self._app.router.add_post("/api/settings", self.api_settings_post)

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if request.path.startswith("/api/") and self.token:
            if request.path in {
                "/api/logs",
                "/api/logs/stream",
                "/api/status",
                "/api/guilds",
                "/api/voices",
                "/api/voices/preview",
                "/api/settings",
            }:
                return await handler(request)
            token = _get_bearer_token(request)
            if token != self.token:
                return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def cog_load(self) -> None:
        if not self.enabled:
            logger.info("Web UI disabled (WEB_UI_ENABLED is falsey).")
            return
        await self.start_server()

    def cog_unload(self) -> None:
        if self._runner is None:
            return
        self.bot.loop.create_task(self.stop_server())

    async def start_server(self) -> None:
        if self._runner is not None:
            return

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self.host, port=self.port)
        await self._site.start()
        logger.info("Web UI listening on http://%s:%s", self.host, self.port)

    async def stop_server(self) -> None:
        if self._runner is None:
            return
        await self._runner.cleanup()
        self._runner = None
        self._site = None
        logger.info("Web UI stopped")

    @property
    def _token_required(self) -> bool:
        return bool(self.token)

    async def page_index(self, request: web.Request) -> web.Response:
        html = _layout("TTS Bot - Home", _index_body(), token_required=self._token_required)
        return web.Response(text=html, content_type="text/html")

    async def page_logs(self, request: web.Request) -> web.Response:
        html = _layout("TTS Bot - Logs", _logs_body(False), token_required=self._token_required)
        return web.Response(text=html, content_type="text/html")

    async def page_settings(self, request: web.Request) -> web.Response:
        html = _layout("TTS Bot - Settings", _settings_body(), token_required=False)
        return web.Response(text=html, content_type="text/html")

    async def api_status(self, request: web.Request) -> web.Response:
        start_time = getattr(self.bot, "start_time", None)
        uptime = 0.0
        if start_time is not None:
            uptime = time.time() - float(start_time)

        data = {
            "user": str(self.bot.user) if self.bot.user else None,
            "tts_version": VERSION,
            "discord_py_version": discord.__version__,
            "guild_count": len(self.bot.guilds),
            "uptime_seconds": uptime,
            "web_host": self.host,
            "web_port": self.port,
        }
        return web.json_response(data)

    async def api_guilds(self, request: web.Request) -> web.Response:
        guilds = [{"id": str(g.id), "name": g.name} for g in self.bot.guilds]
        guilds.sort(key=lambda g: (g.get("name") or "").lower())
        return web.json_response({"guilds": guilds})

    async def api_voices(self, request: web.Request) -> web.Response:
        voices = [{"id": voice_id, "name": name} for voice_id, name in ALL_VOICES]
        return web.json_response({"voices": voices})

    async def api_voice_preview(self, request: web.Request) -> web.StreamResponse:
        voice_id = (request.query.get("voice_id") or "").strip()
        if not voice_id:
            raise web.HTTPBadRequest(text="voice_id is required")

        text = (request.query.get("text") or "").strip()
        if not text:
            friendly = VOICE_ID_TO_NAME.get(voice_id, voice_id)
            text = f"Hello! This is {friendly}."
        text = text.strip()
        if len(text) > 200:
            text = text[:200]

        try:
            stream, producer_task = await get_tts_stream(text, voice_id, fallback_voice=FALLBACK_VOICE)
        except Exception as exc:
            raise web.HTTPBadRequest(text=str(exc))

        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "audio/mpeg",
                "Cache-Control": "no-store",
            },
        )
        await resp.prepare(request)

        try:
            while True:
                chunk = await asyncio.to_thread(stream.read, 64 * 1024)
                if not chunk:
                    break
                await resp.write(chunk)
            await resp.write_eof()
            with contextlib.suppress(Exception):
                await producer_task
        except (ConnectionResetError, asyncio.CancelledError):
            stream.close()
            producer_task.cancel()
            with contextlib.suppress(Exception):
                await producer_task
        finally:
            with contextlib.suppress(Exception):
                stream.close()

        return resp

    async def api_logs(self, request: web.Request) -> web.Response:
        tail = int(request.query.get("tail") or "500")
        buffer = getattr(self.bot, "log_buffer", None)
        lines = buffer.get_lines(tail=tail) if buffer else []
        return web.json_response({"lines": lines})

    async def api_logs_stream(self, request: web.Request) -> web.StreamResponse:
        buffer = getattr(self.bot, "log_buffer", None)
        if buffer is None:
            raise web.HTTPServiceUnavailable(text="Log buffer not configured")

        sub = buffer.subscribe(tail=int(request.query.get("tail") or "500"))

        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)

        try:
            for line in sub.initial_lines:
                await resp.write(_sse_encode(line))

            while True:
                line = await sub.queue.get()
                await resp.write(_sse_encode(line))
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            buffer.unsubscribe(sub.queue)

        return resp

    async def api_settings_get(self, request: web.Request) -> web.Response:
        guild_store = getattr(self.bot, "guild_settings", None)
        if guild_store is not None:
            raw_guild_id = (request.query.get("guild_id") or "").strip()
            if not raw_guild_id:
                raise web.HTTPBadRequest(text="guild_id is required")
            try:
                guild_id = int(raw_guild_id)
            except ValueError:
                raise web.HTTPBadRequest(text="guild_id must be an integer")
            if not self.bot.get_guild(guild_id):
                raise web.HTTPNotFound(text="Unknown guild")
            return web.json_response(await guild_store.get(guild_id))

        store = getattr(self.bot, "settings", None)
        if store is None:
            return web.json_response({})
        return web.json_response(await store.get())

    async def api_settings_post(self, request: web.Request) -> web.Response:
        guild_store = getattr(self.bot, "guild_settings", None)
        if guild_store is not None:
            raw_guild_id = (request.query.get("guild_id") or "").strip()
            if not raw_guild_id:
                raise web.HTTPBadRequest(text="guild_id is required")
            try:
                guild_id = int(raw_guild_id)
            except ValueError:
                raise web.HTTPBadRequest(text="guild_id must be an integer")
            if not self.bot.get_guild(guild_id):
                raise web.HTTPNotFound(text="Unknown guild")

            try:
                payload: Dict[str, Any] = await request.json()
            except json.JSONDecodeError:
                raise web.HTTPBadRequest(text="Invalid JSON")

            try:
                updated = await guild_store.update(guild_id, payload)
            except Exception as exc:
                return web.json_response({"error": str(exc)}, status=400)

            return web.json_response(updated)

        store = getattr(self.bot, "settings", None)
        if store is None:
            raise web.HTTPServiceUnavailable(text="Settings store not configured")

        try:
            payload: Dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text="Invalid JSON")

        try:
            updated = await store.update(payload)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

        return web.json_response(updated)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WebUICog(bot))
