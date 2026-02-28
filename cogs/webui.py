import asyncio
import contextlib
import json
import os
import time
from typing import Any, Dict, Optional

import discord
from aiohttp import web
from discord.ext import commands

from utils.config import ALL_VOICE_IDS, ALL_VOICES, FALLBACK_VOICE, VOICE_ID_TO_NAME
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
        <a class=\"btn\" href=\"/test-voices\">Test Voices</a>
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

    <label class="muted">Allowlist Text Channel IDs</label>
    <input id="allowlistChannels" type="text" placeholder="123, 456, 789" />

  <div class="inputrow" style="margin-top:8px; grid-column:1 / -1;">
      <button class="btn" id="saveBtn">Save Settings</button>
      <span class="muted" id="saveMsg"></span>
    </div>

    <div style="grid-column:1 / -1; height:1px; background: rgba(255,255,255,0.10); margin:6px 0 10px 0;"></div>

    <div style="grid-column:1 / -1;">
      <h3 style="margin:0 0 10px 0;">Allowed Voices</h3>
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
          <input id="voiceFilter" type="text" placeholder="Filter voices…" />
          <input id="previewText" type="text" placeholder="Preview text (optional)" />
          <button class="btn small" id="selectAllVoices">All</button>
          <button class="btn small" id="selectNoneVoices">None</button>
          <button class="btn small" id="previewVoiceBtn">Preview</button>
        </div>
        <select id="allowedVoices" multiple size="12" style="width:100%; min-height:220px;"></select>
        <audio id="voicePlayer" style="width:100%; margin-top:10px; display:none;" controls></audio>
        <p class="muted" style="margin:10px 0 0 0;">
          Tip: Select multiple voices to build the allowlist (Ctrl/Cmd + click).
        </p>
      </div>
    </div>
  </div>

	<script>
	  const guildSelect = document.getElementById('guildSelect');
  const guildPill = document.getElementById('guildPill');
  const elMaxChars = document.getElementById('maxChars');
  const elFallbackVoice = document.getElementById('fallbackVoice');
  const elDefaultVoice = document.getElementById('defaultVoice');
  const elAllowlistChannels = document.getElementById('allowlistChannels');
  const saveMsg = document.getElementById('saveMsg');
  const restrictPill = document.getElementById('restrictPill');
  const voiceCountPill = document.getElementById('voiceCountPill');
  const voiceRestrictBox = document.getElementById('voiceRestrictBox');
  const allowedVoices = document.getElementById('allowedVoices');
	  const voiceFilter = document.getElementById('voiceFilter');
	  const previewText = document.getElementById('previewText');
	  const voicePlayer = document.getElementById('voicePlayer');
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
	  }

	  async function loadVoices() {
	    const res = await apiFetch('/api/voices');
	    const voices = (res && Array.isArray(res.voices)) ? res.voices : [];
	    allVoices = voices.map(v => ({ id: String(v.id), name: String(v.name || v.id) }));
	    renderVoiceSelects();
	    renderAllowedSelect();
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
        const allowlist = Array.isArray(current.allowlist_text_channel_ids) ? current.allowlist_text_channel_ids : [];
        if (elAllowlistChannels) {
          elAllowlistChannels.value = allowlist.join(', ');
        }

		    pill(document.getElementById('autoReadPill'), current.auto_read_messages, current.auto_read_messages ? 'enabled' : 'disabled');
		    pill(document.getElementById('leavePill'), current.leave_when_alone, current.leave_when_alone ? 'enabled' : 'disabled');
		    pill(document.getElementById('greetJoinPill'), current.greet_on_join, current.greet_on_join ? 'enabled' : 'disabled');
		    pill(document.getElementById('farewellLeavePill'), current.farewell_on_leave, current.farewell_on_leave ? 'enabled' : 'disabled');

	    allowedSet = new Set(Array.isArray(current.allowed_voice_ids) ? current.allowed_voice_ids.map(String) : []);
	    updateRestrictUi();
	    renderAllowedSelect();
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

  function renderAllowedSelect() {
    if (!allowedVoices) return;
    allowedVoices.textContent = '';
    if (!allVoices.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No voices loaded.';
      allowedVoices.appendChild(opt);
      updateVoiceCount();
      return;
    }

    syncRequiredVoices();
    const required = new Set(requiredVoiceIds());
    const q = (voiceFilter.value || '').trim().toLowerCase();

    for (const v of allVoices) {
      const hay = (v.name + ' ' + v.id).toLowerCase();
      if (q && !hay.includes(q)) continue;
      const opt = document.createElement('option');
      opt.value = v.id;
      opt.textContent = v.name ? `${v.name} (${v.id})` : v.id;
      if (allowedSet.has(v.id)) opt.selected = true;
      if (required.has(v.id)) {
        opt.selected = true;
        opt.disabled = true;
        allowedSet.add(v.id);
      }
      allowedVoices.appendChild(opt);
    }

    updateVoiceCount();
  }

	  function updateRestrictUi() {
	    if (!current) return;
	    pill(restrictPill, !!current.restrict_voices, current.restrict_voices ? 'enabled' : 'disabled');
	    voiceRestrictBox.style.display = current.restrict_voices ? 'block' : 'none';
	    if (current.restrict_voices) renderAllowedSelect();
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
        if (elAllowlistChannels) {
          const raw = (elAllowlistChannels.value || '').trim();
          const ids = raw ? raw.split(',').map(x => x.trim()).filter(Boolean) : [];
          const cleaned = [];
          const seen = new Set();
          for (const id of ids) {
            const n = parseInt(id, 10);
            if (!Number.isFinite(n) || n <= 0 || seen.has(n)) continue;
            seen.add(n);
            cleaned.push(n);
          }
          payload.allowlist_text_channel_ids = cleaned;
        }

	    return payload;
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
    renderAllowedSelect();
  });

  document.getElementById('selectNoneVoices').addEventListener('click', () => {
    allowedSet = new Set();
    syncRequiredVoices();
    renderAllowedSelect();
  });

  voiceFilter.addEventListener('input', () => {
    if (!current || !current.restrict_voices) return;
    renderAllowedSelect();
  });

  if (allowedVoices) {
    allowedVoices.addEventListener('change', () => {
      const selected = Array.from(allowedVoices.selectedOptions || []).map(opt => opt.value);
      allowedSet = new Set(selected);
      syncRequiredVoices();
      renderAllowedSelect();
    });
  }

  const previewVoiceBtn = document.getElementById('previewVoiceBtn');
  if (previewVoiceBtn) {
    previewVoiceBtn.addEventListener('click', () => {
      if (!allowedVoices) return;
      const selected = Array.from(allowedVoices.selectedOptions || []);
      if (!selected.length) return;
      const vid = selected[0].value;
      voicePlayer.style.display = 'block';
      voicePlayer.src = buildPreviewUrl(vid);
      voicePlayer.play().catch(() => {});
    });
  }

  elFallbackVoice.addEventListener('change', () => {
    if (!current || !current.restrict_voices) return;
    syncRequiredVoices();
    renderAllowedSelect();
  });

	  elDefaultVoice.addEventListener('change', () => {
	    if (!current || !current.restrict_voices) return;
	    syncRequiredVoices();
	    renderAllowedSelect();
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


def _test_voices_body() -> str:
    return """
<div class="card">
  <h2 style="margin:0 0 10px 0;">Test Voices</h2>
  <p class="muted" style="margin:0 0 14px 0;">Test any voice with custom text. Select a server and voice channel to speak in, or just preview the voice audio.</p>
  
  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Server:</label>
    <select id="guildSelect" style="min-width:280px;">
      <option value="">Loading...</option>
    </select>
  </div>
  
  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Voice Channel:</label>
    <select id="channelSelect" style="min-width:280px;">
      <option value="">Select a server first</option>
    </select>
  </div>
  
  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Voice:</label>
    <select id="voiceSelect" style="min-width:280px;">
      <option value="">Loading...</option>
    </select>
  </div>
  
  <div style="margin:0 0 10px 0;">
    <label>Text to speak:</label>
    <textarea id="ttsText" rows="4" placeholder="Enter text to speak..." style="min-height:100px; margin-top:8px;">Hello! This is a test of the text to speech system.</textarea>
  </div>
  
  <div class="inputrow">
    <button class="btn" id="previewBtn">Preview Audio Only</button>
    <button class="btn" id="speakBtn">Speak in Voice Channel</button>
  </div>
  
  <div id="statusMsg" class="muted" style="margin-top:10px;"></div>
  
  <audio id="audioPlayer" controls style="width:100%; margin-top:10px; display:none;"></audio>
</div>

<div class="card" style="margin-top:14px;">
  <h2 style="margin:0 0 10px 0;">Radio Presenter Test</h2>
  <p class="muted" style="margin:0 0 14px 0;">Generate a DJ-style intro and speak it in a Discord voice channel.</p>

  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Server:</label>
    <select id="djGuildSelect" style="min-width:280px;">
      <option value="">Loading...</option>
    </select>
  </div>

  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Voice Channel:</label>
    <select id="djChannelSelect" style="min-width:280px;">
      <option value="">Select a server first</option>
    </select>
  </div>

  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Voice (optional):</label>
    <select id="djVoiceSelect" style="min-width:280px;">
      <option value="">Loading...</option>
    </select>
  </div>

  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Song Name:</label>
    <input id="djSongName" type="text" placeholder="Song title" />
  </div>

  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Artist:</label>
    <input id="djArtist" type="text" placeholder="Artist name" />
  </div>

  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Requested By (optional):</label>
    <input id="djRequestedBy" type="text" placeholder="Requester name" />
  </div>

  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>For (optional):</label>
    <input id="djSongFor" type="text" placeholder="Dedicated to" />
  </div>

  <div class="inputrow">
    <button class="btn" id="djSpeakBtn">Generate + Speak</button>
  </div>

  <div id="djStatusMsg" class="muted" style="margin-top:10px;"></div>
</div>

<div class="card" style="margin-top:14px;">
  <h2 style="margin:0 0 10px 0;">Song Suggestions</h2>
  <p class="muted" style="margin:0 0 14px 0;">Generate 5 similar songs for a seed track.</p>

  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Song Name:</label>
    <input id="suggestSongName" type="text" placeholder="Song title" />
  </div>

  <div class="inputrow" style="margin:0 0 10px 0;">
    <label>Artist:</label>
    <input id="suggestArtist" type="text" placeholder="Artist name" />
  </div>

  <div class="inputrow">
    <button class="btn" id="suggestBtn">Get Suggestions</button>
  </div>

  <div id="suggestStatusMsg" class="muted" style="margin-top:10px;"></div>
  <pre id="suggestResults" class="log" style="max-height:260px; margin-top:10px; display:none;"></pre>
</div>

<div class="card" style="margin-top:14px;">
  <h3 style="margin:0 0 10px 0;">API Usage</h3>
  <p class="muted" style="margin:0 0 10px 0;">External bots can send TTS requests via POST to <code>/api/tts</code></p>
  
  <pre class="log" style="max-height:300px;">POST /api/tts
Content-Type: application/json
Authorization: Bearer YOUR_TOKEN_HERE

{
  "guild_id": "1234567890",
  "channel_id": "9876543210",
  "text": "Hello from external bot!",
  "voice_id": "en_us_001"  // optional
}</pre>
  
  <p class="muted" style="margin:8px 0 0 0;">Response: <code>{"success": true, "message": "TTS queued"}</code></p>

  <p class="muted" style="margin:16px 0 10px 0;">Get song suggestions via POST to <code>/api/song-suggestions</code></p>
  
  <pre class="log" style="max-height:300px;">POST /api/song-suggestions
Content-Type: application/json
Authorization: Bearer YOUR_TOKEN_HERE

{
  "song_name": "Blinding Lights",
  "artist": "The Weeknd"
}</pre>
  
  <p class="muted" style="margin:8px 0 0 0;">Response: <code>{"success": true, "suggestions": [{"title": "Save Your Tears", "artist": "The Weeknd"}, {"title": "Take My Breath", "artist": "The Weeknd"}]}</code></p>
</div>

<script>
  const guildSelect = document.getElementById('guildSelect');
  const channelSelect = document.getElementById('channelSelect');
  const voiceSelect = document.getElementById('voiceSelect');
  const ttsText = document.getElementById('ttsText');
  const statusMsg = document.getElementById('statusMsg');
  const audioPlayer = document.getElementById('audioPlayer');
  const previewBtn = document.getElementById('previewBtn');
  const speakBtn = document.getElementById('speakBtn');
  const djGuildSelect = document.getElementById('djGuildSelect');
  const djChannelSelect = document.getElementById('djChannelSelect');
  const djVoiceSelect = document.getElementById('djVoiceSelect');
  const djSongName = document.getElementById('djSongName');
  const djArtist = document.getElementById('djArtist');
  const djRequestedBy = document.getElementById('djRequestedBy');
  const djSongFor = document.getElementById('djSongFor');
  const djSpeakBtn = document.getElementById('djSpeakBtn');
  const djStatusMsg = document.getElementById('djStatusMsg');
  const suggestSongName = document.getElementById('suggestSongName');
  const suggestArtist = document.getElementById('suggestArtist');
  const suggestBtn = document.getElementById('suggestBtn');
  const suggestStatusMsg = document.getElementById('suggestStatusMsg');
  const suggestResults = document.getElementById('suggestResults');
  
  let guilds = [];
  let voices = [];
  
  function showStatus(msg, isError = false) {
    statusMsg.textContent = msg;
    statusMsg.className = isError ? 'danger' : 'muted';
  }

  function showDjStatus(msg, isError = false) {
    djStatusMsg.textContent = msg;
    djStatusMsg.className = isError ? 'danger' : 'muted';
  }

  function showSuggestStatus(msg, isError = false) {
    suggestStatusMsg.textContent = msg;
    suggestStatusMsg.className = isError ? 'danger' : 'muted';
  }
  
  async function loadGuilds() {
    try {
      const res = await apiFetch('/api/guilds');
      guilds = (res && Array.isArray(res.guilds)) ? res.guilds : [];
      
      guildSelect.textContent = '';
      if (!guilds.length) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No servers available';
        guildSelect.appendChild(opt);
        return;
      }
      
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = 'Select a server...';
      guildSelect.appendChild(placeholder);
      
      for (const g of guilds) {
        const opt = document.createElement('option');
        opt.value = g.id;
        opt.textContent = g.name;
        guildSelect.appendChild(opt);
      }

      djGuildSelect.textContent = '';
      if (!guilds.length) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No servers available';
        djGuildSelect.appendChild(opt);
      } else {
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = 'Select a server...';
        djGuildSelect.appendChild(placeholder);
        for (const g of guilds) {
          const opt = document.createElement('option');
          opt.value = g.id;
          opt.textContent = g.name;
          djGuildSelect.appendChild(opt);
        }
      }
    } catch (e) {
      showStatus('Error loading guilds: ' + e.message, true);
      showDjStatus('Error loading guilds: ' + e.message, true);
    }
  }
  
  async function loadVoices() {
    try {
      const res = await apiFetch('/api/voices');
      voices = (res && Array.isArray(res.voices)) ? res.voices : [];
      
      voiceSelect.textContent = '';
      for (const v of voices) {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = v.name ? `${v.name} (${v.id})` : v.id;
        voiceSelect.appendChild(opt);
      }
      
      if (voices.length > 0) {
        voiceSelect.value = voices[0].id;
      }

      djVoiceSelect.textContent = '';
      const noneOpt = document.createElement('option');
      noneOpt.value = '';
      noneOpt.textContent = 'Default voice';
      djVoiceSelect.appendChild(noneOpt);
      for (const v of voices) {
        const opt = document.createElement('option');
        opt.value = v.id;
        opt.textContent = v.name ? `${v.name} (${v.id})` : v.id;
        djVoiceSelect.appendChild(opt);
      }
    } catch (e) {
      showStatus('Error loading voices: ' + e.message, true);
      showDjStatus('Error loading voices: ' + e.message, true);
    }
  }
  
  guildSelect.addEventListener('change', async () => {
    const guildId = guildSelect.value;
    channelSelect.textContent = '';
    
    if (!guildId) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'Select a server first';
      channelSelect.appendChild(opt);
      return;
    }
    
    // For now, we'll need the bot to provide voice channels via API
    // For simplicity, just let user know to join a channel
    const opt = document.createElement('option');
    opt.value = 'auto';
    opt.textContent = 'Auto-detect (bot joins your channel)';
    channelSelect.appendChild(opt);
    showStatus('Make sure you are in a voice channel in the selected server');
  });

  djGuildSelect.addEventListener('change', async () => {
    const guildId = djGuildSelect.value;
    djChannelSelect.textContent = '';

    if (!guildId) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'Select a server first';
      djChannelSelect.appendChild(opt);
      return;
    }

    const opt = document.createElement('option');
    opt.value = 'auto';
    opt.textContent = 'Auto-detect (bot joins your channel)';
    djChannelSelect.appendChild(opt);
    showDjStatus('Make sure you are in a voice channel in the selected server');
  });
  
  previewBtn.addEventListener('click', async () => {
    const voiceId = voiceSelect.value;
    const text = ttsText.value.trim();
    
    if (!voiceId) {
      showStatus('Please select a voice', true);
      return;
    }
    
    if (!text) {
      showStatus('Please enter text to speak', true);
      return;
    }
    
    try {
      showStatus('Generating audio preview...');
      previewBtn.disabled = true;
      
      const url = '/api/voices/preview?voice_id=' + encodeURIComponent(voiceId) + 
                  '&text=' + encodeURIComponent(text.substring(0, 200));
      
      const headers = authHeaders();
      const response = await fetch(url, { headers });
      
      if (!response.ok) {
        throw new Error('Failed to generate preview: ' + response.statusText);
      }
      
      const blob = await response.blob();
      const audioUrl = URL.createObjectURL(blob);
      
      audioPlayer.src = audioUrl;
      audioPlayer.style.display = 'block';
      audioPlayer.play();
      
      showStatus('Preview ready. Playing audio...');
    } catch (e) {
      showStatus('Error: ' + e.message, true);
    } finally {
      previewBtn.disabled = false;
    }
  });
  
  speakBtn.addEventListener('click', async () => {
    const guildId = guildSelect.value;
    const voiceId = voiceSelect.value;
    const text = ttsText.value.trim();
    
    if (!guildId) {
      showStatus('Please select a server', true);
      return;
    }
    
    if (!voiceId) {
      showStatus('Please select a voice', true);
      return;
    }
    
    if (!text) {
      showStatus('Please enter text to speak', true);
      return;
    }
    
    try {
      showStatus('Sending TTS request...');
      speakBtn.disabled = true;
      
      const payload = {
        guild_id: guildId,
        text: text,
        voice_id: voiceId
      };
      
      const result = await apiFetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      
      showStatus(result.message || 'TTS request sent successfully!');
    } catch (e) {
      showStatus('Error: ' + e.message, true);
    } finally {
      speakBtn.disabled = false;
    }
  });

  djSpeakBtn.addEventListener('click', async () => {
    const guildId = djGuildSelect.value;
    const channelId = djChannelSelect.value;
    const voiceId = djVoiceSelect.value;
    const songName = (djSongName.value || '').trim();
    const artist = (djArtist.value || '').trim();
    const requestedBy = (djRequestedBy.value || '').trim();
    const songFor = (djSongFor.value || '').trim();

    if (!guildId) {
      showDjStatus('Please select a server', true);
      return;
    }
    if (!songName) {
      showDjStatus('Please enter a song name', true);
      return;
    }
    if (!artist) {
      showDjStatus('Please enter an artist name', true);
      return;
    }

    try {
      showDjStatus('Generating DJ intro...');
      djSpeakBtn.disabled = true;

      const payload = {
        guild_id: guildId,
        song_name: songName,
        artist: artist,
      };
      if (channelId && channelId !== 'auto') payload.channel_id = channelId;
      if (voiceId) payload.voice = voiceId;
      if (requestedBy) payload.requested_by = requestedBy;
      if (songFor) payload.song_for = songFor;

      const result = await apiFetch('/api/radio-presenter', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      showDjStatus(result.message || 'DJ intro queued successfully!');
    } catch (e) {
      showDjStatus('Error: ' + e.message, true);
    } finally {
      djSpeakBtn.disabled = false;
    }
  });

  suggestBtn.addEventListener('click', async () => {
    const songName = (suggestSongName.value || '').trim();
    const artist = (suggestArtist.value || '').trim();

    if (!songName) {
      showSuggestStatus('Please enter a song name', true);
      return;
    }
    if (!artist) {
      showSuggestStatus('Please enter an artist name', true);
      return;
    }

    try {
      showSuggestStatus('Generating suggestions...');
      suggestBtn.disabled = true;
      suggestResults.style.display = 'none';
      suggestResults.textContent = '';

      const payload = {
        song_name: songName,
        artist: artist,
      };

      const result = await apiFetch('/api/song-suggestions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const suggestions = (result && Array.isArray(result.suggestions)) ? result.suggestions : [];
      if (!suggestions.length) {
        showSuggestStatus('No suggestions returned.', true);
        return;
      }

      const lines = suggestions.map((s, i) => `${i + 1}. ${s.title} — ${s.artist}`);
      suggestResults.textContent = lines.join('\\n');
      suggestResults.style.display = 'block';
      showSuggestStatus('Suggestions ready.');
    } catch (e) {
      showSuggestStatus('Error: ' + e.message, true);
    } finally {
      suggestBtn.disabled = false;
    }
  });
  
  // Load initial data
  (async () => {
    await loadGuilds();
    await loadVoices();
  })();
</script>
"""


def _obs_player_body() -> str:
    return """
<section class="card">
  <h2>OBS Browser Source Player</h2>
  <p class="muted">Streams TTS audio over WebSocket and plays it in the browser source.</p>
</section>

<section class="card">
  <h3>Connection</h3>
  <div class="inputrow">
    <label>
      WebSocket URL
      <input id="wsUrl" type="text" placeholder="ws://127.0.0.1:8080/ws/tts" />
    </label>
    <label>
      Token (optional)
      <input id="token" type="text" placeholder="WEB_UI_TOKEN" />
    </label>
    <button class="btn" id="connectBtn">Connect</button>
    <button class="btn" id="disconnectBtn">Disconnect</button>
  </div>
  <div class="pill" id="connStatus">Disconnected</div>
</section>

<section class="card">
  <h3>Speak</h3>
  <div class="inputrow">
    <label>
      Voice ID
      <input id="voiceId" type="text" placeholder="google_translate" />
    </label>
  </div>
  <textarea id="ttsText" placeholder="Type text to play in OBS..."></textarea>
  <div class="inputrow" style="margin-top: 10px;">
    <button class="btn" id="sendBtn">Send</button>
    <button class="btn" id="clearBtn">Clear</button>
  </div>
  <p class="muted" id="playStatus">Idle</p>
  <audio id="player" preload="auto"></audio>
</section>

<script>
  const wsUrlEl = document.getElementById('wsUrl');
  const tokenEl = document.getElementById('token');
  const connectBtn = document.getElementById('connectBtn');
  const disconnectBtn = document.getElementById('disconnectBtn');
  const connStatus = document.getElementById('connStatus');
  const voiceIdEl = document.getElementById('voiceId');
  const ttsTextEl = document.getElementById('ttsText');
  const sendBtn = document.getElementById('sendBtn');
  const clearBtn = document.getElementById('clearBtn');
  const playStatus = document.getElementById('playStatus');
  const player = document.getElementById('player');

  let ws = null;
  let chunks = [];

  const setStatus = (text, isError = false) => {
    connStatus.textContent = text;
    connStatus.className = 'pill ' + (isError ? 'warn' : 'ok');
  };

  const defaultWsUrl = () => {
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    return `${scheme}://${window.location.host}/ws/tts`;
  };

  const readQueryToken = () => {
    const params = new URLSearchParams(window.location.search);
    return params.get('token') || '';
  };

  const buildWsUrl = () => {
    const base = wsUrlEl.value.trim() || defaultWsUrl();
    const token = tokenEl.value.trim();
    if (!token) return base;
    const url = new URL(base, window.location.href);
    url.searchParams.set('token', token);
    return url.toString();
  };

  const connect = () => {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const url = buildWsUrl();
    ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => setStatus('Connected');
    ws.onclose = () => setStatus('Disconnected', true);
    ws.onerror = () => setStatus('Error', true);

    ws.onmessage = (evt) => {
      if (typeof evt.data === 'string') {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.event === 'start') {
            chunks = [];
            playStatus.textContent = `Streaming (${msg.voice_id || ''})`;
          } else if (msg.event === 'end') {
            playStatus.textContent = 'Playing';
            const blob = new Blob(chunks, { type: 'audio/mpeg' });
            const url = URL.createObjectURL(blob);
            player.src = url;
            player.play().catch(() => {});
          } else if (msg.event === 'error') {
            playStatus.textContent = 'Error: ' + (msg.error || 'unknown');
          }
        } catch (e) {
          playStatus.textContent = 'Error: invalid JSON message';
        }
      } else {
        chunks.push(evt.data);
      }
    };
  };

  const disconnect = () => {
    if (ws) {
      ws.close();
      ws = null;
    }
  };

  connectBtn.addEventListener('click', connect);
  disconnectBtn.addEventListener('click', disconnect);

  sendBtn.addEventListener('click', () => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      playStatus.textContent = 'Not connected';
      return;
    }
    const text = ttsTextEl.value.trim();
    if (!text) {
      playStatus.textContent = 'Enter text';
      return;
    }
    const payload = {
      text,
      voice_id: voiceIdEl.value.trim(),
    };
    ws.send(JSON.stringify(payload));
    playStatus.textContent = 'Sending...';
  });

  clearBtn.addEventListener('click', () => {
    ttsTextEl.value = '';
    playStatus.textContent = 'Idle';
  });

  // Initialize defaults
  wsUrlEl.value = defaultWsUrl();
  tokenEl.value = readQueryToken();
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
        self._app.router.add_get("/test-voices", self.page_test_voices)
        self._app.router.add_get("/obs", self.page_obs_player)


        self._app.router.add_get("/api/status", self.api_status)
        self._app.router.add_get("/api/guilds", self.api_guilds)
        self._app.router.add_get("/api/voices", self.api_voices)
        self._app.router.add_get("/api/voices/preview", self.api_voice_preview)
        self._app.router.add_get("/api/logs", self.api_logs)
        self._app.router.add_get("/api/logs/stream", self.api_logs_stream)
        self._app.router.add_get("/api/settings", self.api_settings_get)
        self._app.router.add_post("/api/settings", self.api_settings_post)
        self._app.router.add_post("/api/tts", self.api_tts_speak)
        self._app.router.add_get("/ws/tts", self.ws_tts)

        self._app.router.add_post("/api/radio-presenter", self.api_radio_presenter)
        self._app.router.add_post("/api/song-suggestions", self.api_song_suggestions)

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
                "/api/tts",  # Allow TTS requests without auth for testing
                "/api/radio-presenter",  # Allow settings access without auth for testing
                "/api/song-suggestions",
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
        html = _layout("TTS Bot - Logs", _logs_body(False), token_required=False)
        return web.Response(text=html, content_type="text/html")

    async def page_settings(self, request: web.Request) -> web.Response:
        html = _layout("TTS Bot - Settings", _settings_body(), token_required=False)
        return web.Response(text=html, content_type="text/html")

    async def page_test_voices(self, request: web.Request) -> web.Response:
        html = _layout("TTS Bot - Test Voices", _test_voices_body(), token_required=self._token_required)
        return web.Response(text=html, content_type="text/html")

    async def page_obs_player(self, request: web.Request) -> web.Response:
        html = _layout("TTS Bot - OBS Player", _obs_player_body(), token_required=self._token_required)
        return web.Response(text=html, content_type="text/html")
    
    async def api_radio_presenter(self, request: web.Request) -> web.Response:
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(method=request.method, allowed_methods=["POST"])
        try:
            data = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="Invalid JSON body")
        
        song_name = (data.get("song_name") or "").strip()
        artist = (data.get("artist") or "").strip()
        raw_guild_id = str(data.get("guild_id") or "").strip()
        channel_id = data.get("channel_id")
        voice_id = (data.get("voice") or "").strip() or None
        requested_by = data.get("requested_by")
        song_for = data.get("song_for")
        volume = data.get("volume", 0.5)

        if not song_name:
            return web.json_response({"error": "song_name is required"}, status=400)
        if not artist:
            return web.json_response({"error": "artist is required"}, status=400)
        if not raw_guild_id:
            return web.json_response({"error": "guild_id is required"}, status=400)
        try:
            guild_id = int(raw_guild_id)
        except ValueError:
            return web.json_response({"error": "guild_id must be an integer"}, status=400)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return web.json_response({"error": "Unknown guild or bot not in that server"}, status=404)

        from utils.open_ai import dj_intro, dj_intro_fallback
        try:
            text_to_speak, raw_intro, used_fallback = await asyncio.to_thread(
                dj_intro,
                title=song_name,
                artist=artist,
                requested_by=requested_by,
                for_user=song_for,
                return_debug=True,
            )
        except Exception as exc:
            logger.warning("DJ intro generation failed: %s", exc)
            text_to_speak = dj_intro_fallback(
                title=song_name,
                artist=artist,
                requested_by=requested_by,
                for_user=song_for,
            )
            raw_intro = ""
            used_fallback = True

        text_to_speak = (text_to_speak or "").strip()
        if not text_to_speak:
            return web.json_response({"error": "Generated intro was empty"}, status=500)

        if used_fallback:
            logger.warning("DJ intro fallback used for guild %s. raw=%s", guild_id, raw_intro)
        else:
            logger.info("Generated DJ intro for guild %s: %s", guild_id, text_to_speak)

        logger.info("Generated DJ intro for guild %s: %s", guild_id, text_to_speak)

        tts_cog = self.bot.get_cog("TTSCog")
        if not tts_cog:
            return web.json_response({"error": "TTS cog not loaded"}, status=500)

        target_channel = None
        state = tts_cog.get_state(guild_id)

        if channel_id:
            try:
                channel_id = int(channel_id)
                target_channel = guild.get_channel(channel_id)
                if not target_channel or not isinstance(target_channel, discord.VoiceChannel):
                    return web.json_response({"error": "Invalid voice channel"}, status=400)
            except (ValueError, TypeError):
                return web.json_response({"error": "channel_id must be an integer"}, status=400)
        else:
            if state.voice_client and state.voice_client.is_connected():
                target_channel = state.voice_client.channel
            else:
                for channel in guild.voice_channels:
                    if len(channel.members) > 0:
                        target_channel = channel
                        break
                if not target_channel:
                    return web.json_response(
                        {"error": "Bot is not in a voice channel. Join a voice channel first or specify channel_id"},
                        status=400,
                    )

        ok = await tts_cog.ensure_connected(guild, target_channel)
        if not ok:
            locked_id = state.voice_channel_id
            msg = "Bot is currently locked to another voice channel"
            if locked_id:
                msg = f"Bot is locked to channel {locked_id}"
            return web.json_response({"error": msg}, status=409)

        settings = await tts_cog.get_settings(guild_id)
        if voice_id:
            voice_id = tts_cog._effective_voice_id(settings, voice_id, allow_default=True)
        else:
            voice_id = str(settings.get("default_voice_id", FALLBACK_VOICE))

        from cogs.tts import QueueItem
        await state.queue.put(QueueItem(text=text_to_speak, voice_id=voice_id, volume=volume))

        return web.json_response({
            "success": True,
            "message": "DJ intro queued successfully",
            "guild_id": str(guild_id),
            "channel_id": str(target_channel.id),
            "voice_id": voice_id,
            "text": text_to_speak,
            "raw_intro": raw_intro,
            "used_fallback": used_fallback,
        })

    async def api_song_suggestions(self, request: web.Request) -> web.Response:
        if request.method != "POST":
            raise web.HTTPMethodNotAllowed(method=request.method, allowed_methods=["POST"])
        try:
            data = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="Invalid JSON body")

        song_name = (data.get("song_name") or "").strip()
        artist = (data.get("artist") or "").strip()

        if not song_name:
            return web.json_response({"error": "song_name is required"}, status=400)
        if not artist:
            return web.json_response({"error": "artist is required"}, status=400)

        from utils.open_ai import song_suggestions
        try:
            suggestions, raw, used_fallback = await asyncio.to_thread(
                song_suggestions,
                title=song_name,
                artist=artist,
                return_debug=True,
            )
        except Exception as exc:
            logger.warning("Song suggestions failed: %s", exc)
            suggestions, raw, used_fallback = [], "", True

        return web.json_response({
            "success": True,
            "song_name": song_name,
            "artist": artist,
            "suggestions": suggestions,
            "raw": raw,
            "used_fallback": used_fallback,
        })



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

    async def api_tts_speak(self, request: web.Request) -> web.Response:
        """API endpoint for external bots to send TTS requests."""
        try:
            payload: Dict[str, Any] = await request.json()
        except json.JSONDecodeError:
            raise web.HTTPBadRequest(text="Invalid JSON")

        # Extract parameters
        raw_guild_id = str(payload.get("guild_id", "")).strip()
        if not raw_guild_id:
            return web.json_response({"error": "guild_id is required"}, status=400)

        try:
            guild_id = int(raw_guild_id)
        except ValueError:
            return web.json_response({"error": "guild_id must be an integer"}, status=400)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return web.json_response({"error": "Unknown guild or bot not in that server"}, status=404)

        text = str(payload.get("text", "")).strip()
        if not text:
            return web.json_response({"error": "text is required"}, status=400)

        voice_id = str(payload.get("voice_id", "")).strip() or None
        channel_id = payload.get("channel_id")

        # Get the TTS cog
        tts_cog = self.bot.get_cog("TTSCog")
        if not tts_cog:
            return web.json_response({"error": "TTS cog not loaded"}, status=500)

        # Determine target channel
        target_channel = None
        state = tts_cog.get_state(guild_id)
        
        if channel_id:
            # Specific channel requested
            try:
                channel_id = int(channel_id)
                target_channel = guild.get_channel(channel_id)
                if not target_channel or not isinstance(target_channel, discord.VoiceChannel):
                    return web.json_response({"error": "Invalid voice channel"}, status=400)
            except (ValueError, TypeError):
                return web.json_response({"error": "channel_id must be an integer"}, status=400)
        else:
            # Check if bot is already connected to a channel in this guild
            if state.voice_client and state.voice_client.is_connected():
                target_channel = state.voice_client.channel
            else:
                # Try to find any voice channel with members
                for channel in guild.voice_channels:
                    if len(channel.members) > 0:
                        target_channel = channel
                        break
                
                if not target_channel:
                    return web.json_response(
                        {"error": "Bot is not in a voice channel. Join a voice channel first or specify channel_id"},
                        status=400
                    )

        # Ensure bot is connected (will use existing connection if already in that channel)
        ok = await tts_cog.ensure_connected(guild, target_channel)
        if not ok:
            locked_id = state.voice_channel_id
            msg = "Bot is currently locked to another voice channel"
            if locked_id:
                msg = f"Bot is locked to channel {locked_id}"
            return web.json_response({"error": msg}, status=409)

        # Get settings and determine voice
        settings = await tts_cog.get_settings(guild_id)
        if voice_id:
            voice_id = tts_cog._effective_voice_id(settings, voice_id, allow_default=True)
        else:
            # Use default voice
            voice_id = str(settings.get("default_voice_id", FALLBACK_VOICE))

        # Queue the TTS
        state = tts_cog.get_state(guild_id)
        from cogs.tts import QueueItem
        await state.queue.put(QueueItem(text=text, voice_id=voice_id))

        return web.json_response({
            "success": True,
            "message": "TTS queued successfully",
            "guild_id": str(guild_id),
            "channel_id": str(target_channel.id),
            "voice_id": voice_id
        })

    async def ws_tts(self, request: web.Request) -> web.StreamResponse:
        if self._token_required:
            token = _get_bearer_token(request)
            if token != self.token:
                return web.json_response({"error": "unauthorized"}, status=401)

        ws = web.WebSocketResponse(heartbeat=20, receive_timeout=60)
        await ws.prepare(request)

        async def stream_tts(text: str, voice_id: Optional[str]) -> None:
            requested_voice = (voice_id or "").strip() or FALLBACK_VOICE
            if requested_voice not in ALL_VOICE_IDS:
                await ws.send_json({"event": "error", "error": f"Unknown voice_id: {requested_voice}"})
                return

            try:
                stream, producer_task = await get_tts_stream(text, requested_voice)
            except Exception as exc:
                logger.warning("WebSocket TTS stream failed to start: %s", exc)
                await ws.send_json({"event": "error", "error": str(exc)})
                return

            await ws.send_json({"event": "start", "voice_id": requested_voice, "format": "mp3"})

            try:
                while True:
                    chunk = await asyncio.to_thread(stream.read, 4096)
                    if not chunk:
                        break
                    await ws.send_bytes(chunk)
                await producer_task
                await ws.send_json({"event": "end"})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WebSocket TTS stream error: %s", exc)
                with contextlib.suppress(Exception):
                    await ws.send_json({"event": "error", "error": str(exc)})
            finally:
                if not producer_task.done():
                    producer_task.cancel()
                    with contextlib.suppress(Exception):
                        await producer_task

        in_flight: Optional[asyncio.Task] = None
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        payload = json.loads(msg.data)
                    except json.JSONDecodeError:
                        await ws.send_json({"event": "error", "error": "Invalid JSON"})
                        continue

                    text = str(payload.get("text", "")).strip()
                    voice_id = str(payload.get("voice_id", "")).strip() or None
                    if not text:
                        await ws.send_json({"event": "error", "error": "text is required"})
                        continue

                    if in_flight and not in_flight.done():
                        in_flight.cancel()
                        with contextlib.suppress(Exception):
                            await in_flight

                    in_flight = asyncio.create_task(stream_tts(text, voice_id))
                elif msg.type == web.WSMsgType.ERROR:
                    logger.warning("WebSocket connection error: %s", ws.exception())
                    break
                elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.CLOSING, web.WSMsgType.CLOSED):
                    break
        finally:
            if in_flight and not in_flight.done():
                in_flight.cancel()
                with contextlib.suppress(Exception):
                    await in_flight

        return ws


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WebUICog(bot))
