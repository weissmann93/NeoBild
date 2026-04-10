#!/usr/bin/env python3
"""TrinityCore Web UI — config editor, engine control, live log via SSE."""
import json, os, re, socket, subprocess, threading, time
from flask import Flask, Response, request, jsonify

_ANSI = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

app        = Flask(__name__)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE   = os.path.join(SCRIPT_DIR, "config.json")
ENGINE     = os.path.join(SCRIPT_DIR, "engine.py")
SECRETS    = os.path.expanduser("~/.termux_secrets")

_proc      = None          # engine subprocess
_proc_lock = threading.Lock()
_buf       = []            # ring-buffer of log lines
_buf_lock  = threading.Lock()
_MAX_BUF   = 600

# ── Secrets ───────────────────────────────────────────────────────────────────

def read_secret(key):
    if not os.path.exists(SECRETS):
        return ""
    with open(SECRETS) as f:
        for line in f:
            k, _, v = line.strip().partition("=")
            if k == key:
                return v
    return ""

def write_secret(key, value):
    lines = []
    if os.path.exists(SECRETS):
        with open(SECRETS) as f:
            lines = [l for l in f.readlines() if not l.startswith(key + "=")]
    lines.append(f"{key}={value}\n")
    with open(SECRETS, "w") as f:
        f.writelines(lines)

# ── Engine process ────────────────────────────────────────────────────────────

def _reader(proc):
    for raw in iter(proc.stdout.readline, b""):
        line = _ANSI.sub("", raw.decode("utf-8", errors="replace").rstrip())
        if line:
            with _buf_lock:
                _buf.append(line)
                if len(_buf) > _MAX_BUF:
                    del _buf[: len(_buf) - _MAX_BUF]
    with _buf_lock:
        _buf.append("[engine stopped]")

def _start_engine(cfg_path):
    global _proc
    env = os.environ.copy()
    api_key = read_secret("MNN_API_KEY")
    if api_key:
        env["MNN_API_KEY"] = api_key
    proc = subprocess.Popen(
        ["python3", ENGINE, cfg_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env,
    )
    _proc = proc
    threading.Thread(target=_reader, args=(proc,), daemon=True).start()

# ── MNN status ────────────────────────────────────────────────────────────────

def mnn_alive():
    try:
        s = socket.create_connection(("127.0.0.1", 8080), timeout=1)
        s.close()
        return True
    except OSError:
        return False

# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/api/config", methods=["GET"])
def api_config_get():
    if not os.path.exists(CFG_FILE):
        return jsonify({}), 404
    with open(CFG_FILE) as f:
        return jsonify(json.load(f))

@app.route("/api/config", methods=["POST"])
def api_config_post():
    data = request.get_json(force=True)
    tmp  = CFG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_FILE)
    return jsonify({"ok": True})

@app.route("/api/apikey", methods=["GET"])
def api_key_get():
    return jsonify({"value": read_secret("MNN_API_KEY")})

@app.route("/api/apikey", methods=["POST"])
def api_key_post():
    write_secret("MNN_API_KEY", request.get_json(force=True).get("value", ""))
    return jsonify({"ok": True})

@app.route("/api/status")
def api_status():
    with _proc_lock:
        running = _proc is not None and _proc.poll() is None
    return jsonify({"mnn": mnn_alive(), "engine": running})

@app.route("/api/start", methods=["POST"])
def api_start():
    global _proc
    with _proc_lock:
        if _proc and _proc.poll() is None:
            return jsonify({"ok": False, "msg": "already running"})
        cfg = request.get_json(force=True).get("config", CFG_FILE)
        _start_engine(cfg)
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _proc
    with _proc_lock:
        if _proc and _proc.poll() is None:
            _proc.terminate()
            return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "not running"})

@app.route("/api/stream")
def api_stream():
    def gen():
        cursor = 0
        while True:
            with _buf_lock:
                chunk = _buf[cursor:]
                cursor += len(chunk)
            for line in chunk:
                yield f"data: {line}\n\n"
            time.sleep(0.4)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── UI ────────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>TrinityCore</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{--bg:#0d0d0d;--bg2:#111;--bg3:#1a1a1a;--border:#222;--text:#ccc;--dim:#666;--green:#00ff41;--red:#ff4444;--accent:#00ff41}
body{background:var(--bg);color:var(--text);font:16px/1.5 'Courier New',monospace;display:flex;flex-direction:column;height:100dvh;overflow:hidden}

/* ── Topbar (always visible) ── */
#topbar{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 14px;display:flex;align-items:center;gap:10px;flex-shrink:0}
#topbar h1{font-size:13px;letter-spacing:2px;color:#eee;white-space:nowrap}
.dot{width:10px;height:10px;border-radius:50%;background:#333;flex-shrink:0}
.dot.green{background:var(--green)}.dot.red{background:var(--red)}
.dot-label{font-size:12px;color:var(--dim)}
#btn-engine{margin-left:auto;padding:10px 18px;font:15px 'Courier New',monospace;border-radius:4px;border:1px solid;cursor:pointer;white-space:nowrap;min-height:44px}
.btn-start{border-color:#00ff4166;color:var(--green);background:transparent}
.btn-stop{border-color:#ff444466;color:var(--red);background:transparent}

/* ── Tabs ── */
#tabbar{background:var(--bg2);border-bottom:2px solid var(--border);display:flex;flex-shrink:0}
.tab{flex:1;padding:14px 0;text-align:center;font:14px 'Courier New',monospace;color:#555;cursor:pointer;letter-spacing:1px;border-bottom:2px solid transparent;margin-bottom:-2px;user-select:none}
.tab.active{color:#ffffff;border-bottom-color:var(--green)}

/* ── Tab panels ── */
.panel{display:none;flex:1;overflow-y:auto;padding:16px 14px}
.panel.active{display:block}
#tab-log.active{display:flex;flex-direction:column;padding:0}

/* ── Form elements ── */
section{margin-bottom:22px}
h2{font-size:12px;letter-spacing:1px;color:var(--green);text-transform:uppercase;margin-bottom:10px;border-bottom:1px solid #1e1e1e;padding-bottom:5px}
label{display:block;font-size:14px;color:#aaaaaa;margin-bottom:5px}
input[type=text],input[type=number],input[type=password],select,textarea{
  width:100%;background:#1a1a1a;border:1px solid #444;color:#ffffff;
  padding:12px;font:16px 'Courier New',monospace;border-radius:4px;outline:none;
  resize:vertical;-webkit-appearance:none;appearance:none}
input:focus,textarea:focus,select:focus{border-color:#444}
textarea{min-height:80px}
.field{margin-bottom:14px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.check-row{display:flex;align-items:center;gap:12px;padding:10px 0}
.check-row input[type=checkbox]{width:22px;height:22px;accent-color:var(--green);flex-shrink:0}
.check-row span{font-size:16px;color:#999}

/* ── Buttons ── */
button{font:16px 'Courier New',monospace;border-radius:4px;cursor:pointer;
  padding:12px 18px;border:1px solid var(--border);background:var(--bg3);color:var(--text);
  width:100%;min-height:48px;margin-top:8px}
button:active{background:#222}
.btn-save{border-color:#00ff4155;color:var(--green)}
.btn-secondary{color:#888;font-size:14px}
.btn-danger{border-color:#ff444455;color:var(--red)}

/* ── Persona cards ── */
.persona-card{background:#1e1e1e;border:1px solid #333;border-radius:6px;padding:14px;margin-bottom:12px}
.persona-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.persona-hdr span{color:var(--green);font-size:13px;letter-spacing:1px}
.rm-btn{background:none;border:none;color:var(--dim);cursor:pointer;font-size:22px;padding:0 6px;width:auto;min-height:auto;margin:0}

/* ── Log panel ── */
#log-toolbar{padding:10px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px;flex-shrink:0;background:var(--bg2)}
#log-lines{font-size:13px;color:var(--dim)}
#autoscroll-label{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:14px;color:var(--dim);cursor:pointer;user-select:none}
#autoscroll-label input{width:20px;height:20px;accent-color:var(--green)}
#log{flex:1;overflow-y:auto;padding:12px 14px;font-size:15px;line-height:1.7;white-space:pre-wrap;word-break:break-all;color:#e0e0e0}
.log-system{color:#444;font-style:italic}
.log-ok{color:var(--green)}
.log-err{color:var(--red)}

/* ── Toast ── */
#toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
  background:#1a1a1a;border:1px solid #333;color:#ccc;padding:10px 20px;
  border-radius:6px;font-size:14px;opacity:0;transition:opacity .3s;pointer-events:none;white-space:nowrap}
#toast.show{opacity:1}
</style>
</head>
<body>

<!-- Always-visible top bar -->
<div id="topbar">
  <h1>TRINITY</h1>
  <div class="dot" id="dot-mnn"></div>
  <span class="dot-label" id="lbl-mnn">MNN</span>
  <div class="dot" id="dot-eng"></div>
  <span class="dot-label" id="lbl-eng">ENG</span>
  <button id="btn-engine" class="btn-start" onclick="toggleEngine()">START</button>
</div>

<!-- Tab bar -->
<div id="tabbar">
  <div class="tab active" onclick="showTab('config')">CONFIG</div>
  <div class="tab" onclick="showTab('personas')">PERSONAS</div>
  <div class="tab" onclick="showTab('log')">LOG</div>
</div>

<!-- CONFIG tab -->
<div class="panel active" id="tab-config">

  <section>
    <h2>General</h2>
    <div class="field"><label>Task</label><input type="text" id="f-task" placeholder="task description"></div>
    <div class="row2">
      <div class="field"><label>Rounds</label><input type="number" id="f-rounds" min="1" value="10"></div>
      <div class="field"><label>Log file</label><input type="text" id="f-logfile" placeholder="logs/trinity.md"></div>
    </div>
    <div class="check-row">
      <input type="checkbox" id="f-blake3" checked>
      <span>BLAKE3 hash-chaining</span>
    </div>
  </section>

  <section>
    <h2>LLM</h2>
    <div class="field"><label>URL</label><input type="text" id="f-llm-url" value="http://127.0.0.1:8080"></div>
    <div class="field"><label>Model</label><input type="text" id="f-llm-model" placeholder="model name"></div>
    <div class="row2">
      <div class="field"><label>Temperature</label><input type="number" id="f-llm-temp" min="0" max="2" step="0.05" value="0.4"></div>
      <div class="field"><label>Max tokens</label><input type="number" id="f-llm-tokens" min="50" value="300"></div>
    </div>
  </section>

  <section>
    <h2>Input Source</h2>
    <div class="field"><label>Type</label>
      <select id="f-src-type" onchange="toggleSrcFields()">
        <option value="static">static</option>
        <option value="cisa_json">cisa_json</option>
      </select>
    </div>
    <div class="field" id="src-url-row" style="display:none">
      <label>URL</label><input type="text" id="f-src-url">
    </div>
    <div class="field" id="src-items-row">
      <label id="src-items-label">Items (one per line)</label>
      <textarea id="f-src-items" rows="5"></textarea>
    </div>
  </section>

  <section>
    <h2>Config File</h2>
    <div class="field"><label>Path (leave empty for default)</label>
      <input type="text" id="f-cfgpath" placeholder="…/trinity_core/config.json">
    </div>
  </section>

  <section>
    <h2>API Key</h2>
    <div class="field"><label>MNN_API_KEY (~/.termux_secrets)</label>
      <input type="password" id="f-apikey" placeholder="leave empty if not needed">
    </div>
    <button class="btn-secondary" onclick="saveApiKey()">Save API Key</button>
  </section>

  <button class="btn-save" onclick="saveConfig()">Save Config</button>
  <div style="height:20px"></div>
</div>

<!-- PERSONAS tab -->
<div class="panel" id="tab-personas">
  <div id="personas-list"></div>
  <button class="btn-secondary" onclick="addPersona()">+ Add Persona</button>
  <button class="btn-save" onclick="saveConfig(true)">Save Config</button>
  <div style="height:20px"></div>
</div>

<!-- LOG tab -->
<div class="panel" id="tab-log">
  <div id="log-toolbar">
    <span id="log-lines">0 lines</span>
    <label id="autoscroll-label">
      <input type="checkbox" id="autoscroll" checked>auto-scroll
    </label>
  </div>
  <div id="log"></div>
</div>

<div id="toast"></div>

<script>
const $ = id => document.getElementById(id);
let lineCount = 0;
let engineRunning = false;

// ── Tabs ──────────────────────────────────────────────────────────────────────

function showTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => {
    const names = ['config','personas','log'];
    t.classList.toggle('active', names[i] === name);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  $('tab-' + name).classList.add('active');
}

// ── Toast ─────────────────────────────────────────────────────────────────────

function toast(msg, ok=true) {
  const t = $('toast');
  t.textContent = msg;
  t.style.borderColor = ok ? '#00ff4155' : '#ff444455';
  t.style.color = ok ? '#00ff41' : '#ff4444';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2000);
}

// ── Config load/save ──────────────────────────────────────────────────────────

async function loadConfig() {
  const r = await fetch('/api/config');
  if (!r.ok) return;
  const c = await r.json();
  $('f-task').value     = c.task || '';
  $('f-rounds').value   = c.rounds || 10;
  $('f-logfile').value  = c.log_file || 'logs/trinity.md';
  $('f-blake3').checked = c.log_blake3 !== false;
  if (c.llm) {
    $('f-llm-url').value    = c.llm.url || 'http://127.0.0.1:8080';
    $('f-llm-model').value  = c.llm.model || '';
    $('f-llm-temp').value   = c.llm.temperature ?? 0.4;
    $('f-llm-tokens').value = c.llm.max_tokens || 300;
  }
  const src = c.input_source || {};
  $('f-src-type').value = src.type || 'static';
  toggleSrcFields();
  if (src.url) $('f-src-url').value = src.url;
  $('f-src-items').value = (src.items || src.fallback || []).join('\n');
  $('personas-list').innerHTML = '';
  (c.personas || []).forEach(p => addPersona(p));
  if (!$('f-cfgpath').value)
    $('f-cfgpath').value = '/data/data/com.termux/files/home/NeoBild/trinity_core/config.json';
}

function buildConfig() {
  const src_type = $('f-src-type').value;
  const items = $('f-src-items').value.split('\n').map(s=>s.trim()).filter(Boolean);
  const src = src_type === 'cisa_json'
    ? {type:'cisa_json', url:$('f-src-url').value, fallback:items}
    : {type:'static', items};
  const personas = [...document.querySelectorAll('.persona-card')].map(card => ({
    name:          card.querySelector('.p-name').value,
    role:          card.querySelector('.p-role').value,
    system_prompt: card.querySelector('.p-prompt').value,
  }));
  return {
    task:       $('f-task').value,
    rounds:     parseInt($('f-rounds').value) || 10,
    log_file:   $('f-logfile').value || 'logs/trinity.md',
    log_blake3: $('f-blake3').checked,
    llm: {
      url:         $('f-llm-url').value,
      model:       $('f-llm-model').value,
      temperature: parseFloat($('f-llm-temp').value) || 0.4,
      max_tokens:  parseInt($('f-llm-tokens').value) || 300,
    },
    input_source: src,
    personas,
  };
}

async function saveConfig(switchToLog=false) {
  const r = await fetch('/api/config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(buildConfig())
  });
  const d = await r.json();
  toast(d.ok ? 'Config saved' : 'Save failed', d.ok);
  appendLog(d.ok ? '[config saved]' : '[save failed]', d.ok ? 'log-ok' : 'log-err');
}

// ── Personas ──────────────────────────────────────────────────────────────────

function addPersona(p={}) {
  const card = document.createElement('div');
  card.className = 'persona-card';
  card.innerHTML = `
    <div class="persona-hdr">
      <span>PERSONA</span>
      <button class="rm-btn" onclick="this.closest('.persona-card').remove()">✕</button>
    </div>
    <div class="row2">
      <div class="field"><label>Name</label><input class="p-name" type="text" value="${esc(p.name||'')}"></div>
      <div class="field"><label>Role</label><input class="p-role" type="text" value="${esc(p.role||'')}"></div>
    </div>
    <div class="field"><label>System Prompt</label><textarea class="p-prompt">${esc(p.system_prompt||'')}</textarea></div>`;
  $('personas-list').appendChild(card);
}

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

// ── Input source toggle ───────────────────────────────────────────────────────

function toggleSrcFields() {
  const isCisa = $('f-src-type').value === 'cisa_json';
  $('src-url-row').style.display = isCisa ? 'block' : 'none';
  $('src-items-label').textContent = isCisa ? 'Fallback items (one per line)' : 'Items (one per line)';
}

// ── API key ───────────────────────────────────────────────────────────────────

async function loadApiKey() {
  const d = await (await fetch('/api/apikey')).json();
  $('f-apikey').value = d.value || '';
}

async function saveApiKey() {
  await fetch('/api/apikey', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({value: $('f-apikey').value})});
  toast('API key saved');
  appendLog('[API key saved]', 'log-ok');
}

// ── Engine start/stop ─────────────────────────────────────────────────────────

async function toggleEngine() {
  if (engineRunning) {
    await fetch('/api/stop', {method:'POST'});
    appendLog('[stop requested]', 'log-system');
    toast('Engine stopping', false);
  } else {
    const cfg = $('f-cfgpath').value.trim();
    await fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({config: cfg || undefined})});
    appendLog('[engine starting...]', 'log-ok');
    toast('Engine starting');
    showTab('log');
  }
}

// ── Live log SSE ──────────────────────────────────────────────────────────────

function appendLog(text, cls='') {
  const div = document.createElement('div');
  if (cls) div.className = cls;
  div.textContent = text;
  $('log').appendChild(div);
  lineCount++;
  $('log-lines').textContent = lineCount + ' lines';
  if ($('autoscroll').checked) $('log').scrollTop = $('log').scrollHeight;
}

function connectSSE() {
  const es = new EventSource('/api/stream');
  es.onmessage = e => {
    const t = e.data;
    const cls = t.startsWith('[engine') ? 'log-system'
              : t.includes('ERROR') || t.includes('BROKEN') ? 'log-err'
              : t.includes('Chain:') || t.includes('[chain]') ? 'log-ok' : '';
    appendLog(t, cls);
  };
  es.onerror = () => {
    appendLog('[stream disconnected, reconnecting...]', 'log-err');
    es.close();
    setTimeout(connectSSE, 3000);
  };
}

// ── Status poll ───────────────────────────────────────────────────────────────

async function pollStatus() {
  try {
    const d = await (await fetch('/api/status')).json();
    $('dot-mnn').className = 'dot ' + (d.mnn ? 'green' : 'red');
    $('lbl-mnn').textContent = 'MNN';
    engineRunning = d.engine;
    $('dot-eng').className = 'dot ' + (d.engine ? 'green' : 'red');
    $('lbl-eng').textContent = 'ENG';
    $('btn-engine').textContent = d.engine ? 'STOP' : 'START';
    $('btn-engine').className   = d.engine ? 'btn-stop' : 'btn-start';
  } catch(_) {}
  setTimeout(pollStatus, 4000);
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadConfig();
loadApiKey();
connectSSE();
pollStatus();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return HTML


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5001
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
