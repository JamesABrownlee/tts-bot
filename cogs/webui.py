import asyncio
import json
import os
import time
from typing import Any, Dict, Optional

from aiohttp import web
from discord.ext import commands

from utils.logger import get_logger

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
    input[type=text], input[type=number] {{
      background: rgba(0,0,0,0.28);
      color: var(--text);
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 12px;
      padding: 10px 12px;
      min-width: 240px;
      outline: none;
    }}

    input[type=text]:focus, input[type=number]:focus {{ border-color: rgba(69,208,255,0.35); box-shadow: 0 0 0 3px rgba(69,208,255,0.12); }}

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
  </style>
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
      If you configured <code>WEB_UI_TOKEN</code>, enter it here. It is stored in your browser's localStorage.
    </p>
    <div class="inputrow">
      <input id="tokenInput" type="text" placeholder="WEB_UI_TOKEN (optional)" />
      <button class="btn" id="saveToken">Save</button>
      <button class="btn" id="clearToken">Clear</button>
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
  tokenInput.value = localStorage.getItem('web_token') || '';

  document.getElementById('saveToken').addEventListener('click', () => {
    localStorage.setItem('web_token', tokenInput.value || '');
    location.reload();
  });

  document.getElementById('clearToken').addEventListener('click', () => {
    localStorage.removeItem('web_token');
    location.reload();
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
        "<span class=\"pill warn\">Token required for streaming</span>" if token_required else "<span class=\"pill ok\">Streaming enabled</span>"
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

  let paused = false;
  let es = null;

  function connect() {
    const t = getToken();
    const url = window.__TOKEN_REQUIRED__ && t ? ('/api/logs/stream?token=' + encodeURIComponent(t)) : '/api/logs/stream';
    es = new EventSource(url);

    es.onmessage = (ev) => {
      if (paused) return;
      logBox.textContent += (logBox.textContent.endsWith('\n') || logBox.textContent.length === 0) ? ev.data + '\n' : '\n' + ev.data + '\n';
      logBox.scrollTop = logBox.scrollHeight;
    };

    es.onerror = () => {
      if (es) es.close();
      es = null;
      if (!paused) {
        logBox.textContent += "\n[webui] disconnected — retrying in 2s\n";
        setTimeout(connect, 2000);
      }
    };

    logBox.textContent = '';
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

  connect();
</script>
"""

    return html.replace("__TOKEN_HINT__", token_hint)


def _settings_body() -> str:
    return """
<div class="card">
  <h2 style="margin:0 0 10px 0;">Settings</h2>
  <p class="muted" style="margin:0 0 14px 0;">Edits are saved to <code>settings.json</code> (or <code>SETTINGS_PATH</code>).</p>

  <div class="grid" style="gap:10px;">
    <label class="muted">Max TTS Characters</label>
    <input id="maxChars" type="number" min="1" max="2000" />

    <label class="muted">Fallback Voice ID</label>
    <input id="fallbackVoice" type="text" />

    <label class="muted">Default Voice ID (used on join)</label>
    <input id="defaultVoice" type="text" />

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

    <div class="inputrow" style="margin-top:8px;">
      <button class="btn" id="saveBtn">Save Settings</button>
      <span class="muted" id="saveMsg"></span>
    </div>
  </div>
</div>

<script>
  const elMaxChars = document.getElementById('maxChars');
  const elFallbackVoice = document.getElementById('fallbackVoice');
  const elDefaultVoice = document.getElementById('defaultVoice');
  const saveMsg = document.getElementById('saveMsg');

  let current = null;

  function pill(el, ok, text) {
    el.textContent = text;
    el.classList.remove('ok', 'warn');
    el.classList.add(ok ? 'ok' : 'warn');
  }

  async function loadSettings() {
    current = await apiFetch('/api/settings');
    elMaxChars.value = current.max_tts_chars;
    elFallbackVoice.value = current.fallback_voice;
    elDefaultVoice.value = current.default_voice_id;

    pill(document.getElementById('autoReadPill'), current.auto_read_messages, current.auto_read_messages ? 'enabled' : 'disabled');
    pill(document.getElementById('leavePill'), current.leave_when_alone, current.leave_when_alone ? 'enabled' : 'disabled');
  }

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

  document.getElementById('saveBtn').addEventListener('click', async () => {
    saveMsg.textContent = '';
    try {
      const payload = {
        max_tts_chars: parseInt(elMaxChars.value || '300', 10),
        fallback_voice: (elFallbackVoice.value || '').trim(),
        default_voice_id: (elDefaultVoice.value || '').trim(),
        auto_read_messages: !!current.auto_read_messages,
        leave_when_alone: !!current.leave_when_alone,
      };
      current = await apiFetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      saveMsg.textContent = 'Saved.';
    } catch (e) {
      saveMsg.textContent = 'Error: ' + e.message;
      saveMsg.className = 'danger';
    }
  });

  loadSettings().catch(e => {
    saveMsg.textContent = 'Error: ' + e.message;
    saveMsg.className = 'danger';
  });
</script>
"""


class WebUICog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.enabled = _truthy(os.getenv("WEB_UI_ENABLED"), default=True)
        self.host = os.getenv("WEB_HOST") or "127.0.0.1"
        self.port = int(os.getenv("WEB_PORT") or "5090")
        self.token = os.getenv("WEB_UI_TOKEN")

        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        self._app = web.Application(middlewares=[self._auth_middleware])
        self._app.router.add_get("/", self.page_index)
        self._app.router.add_get("/logs", self.page_logs)
        self._app.router.add_get("/settings", self.page_settings)

        self._app.router.add_get("/api/status", self.api_status)
        self._app.router.add_get("/api/logs", self.api_logs)
        self._app.router.add_get("/api/logs/stream", self.api_logs_stream)
        self._app.router.add_get("/api/settings", self.api_settings_get)
        self._app.router.add_post("/api/settings", self.api_settings_post)

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if request.path.startswith("/api/") and self.token:
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
        html = _layout("TTS Bot - Logs", _logs_body(self._token_required), token_required=self._token_required)
        return web.Response(text=html, content_type="text/html")

    async def page_settings(self, request: web.Request) -> web.Response:
        html = _layout("TTS Bot - Settings", _settings_body(), token_required=self._token_required)
        return web.Response(text=html, content_type="text/html")

    async def api_status(self, request: web.Request) -> web.Response:
        start_time = getattr(self.bot, "start_time", None)
        uptime = 0.0
        if start_time is not None:
            uptime = time.time() - float(start_time)

        data = {
            "user": str(self.bot.user) if self.bot.user else None,
            "guild_count": len(self.bot.guilds),
            "uptime_seconds": uptime,
            "web_host": self.host,
            "web_port": self.port,
        }
        return web.json_response(data)

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
        store = getattr(self.bot, "settings", None)
        if store is None:
            return web.json_response({})
        return web.json_response(await store.get())

    async def api_settings_post(self, request: web.Request) -> web.Response:
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
