#!/usr/bin/env python3
"""TrinityCore Web UI — config editor, engine control, live log via SSE."""
import json, os, socket, subprocess, threading, time
from flask import Flask, Response, request, jsonify

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
        line = raw.decode("utf-8", errors="replace").rstrip()
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
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TrinityCore UI</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#ccc;font:13px/1.5 'Courier New',monospace;display:flex;flex-direction:column;height:100vh;overflow:hidden}
header{background:#111;border-bottom:1px solid #222;padding:10px 16px;display:flex;align-items:center;gap:12px;flex-shrink:0}
header h1{font-size:14px;letter-spacing:2px;color:#eee}
.dot{width:9px;height:9px;border-radius:50%;background:#333;flex-shrink:0}
.dot.green{background:#00ff41}.dot.red{background:#ff4444}
.label{font-size:11px;color:#555}
.main{display:flex;flex:1;overflow:hidden}
.panel{padding:14px;overflow-y:auto}
.left{width:42%;border-right:1px solid #1a1a1a;flex-shrink:0}
.right{flex:1;display:flex;flex-direction:column}
section{margin-bottom:18px}
h2{font-size:11px;letter-spacing:1px;color:#555;text-transform:uppercase;margin-bottom:8px;border-bottom:1px solid #1a1a1a;padding-bottom:4px}
label{display:block;font-size:11px;color:#888;margin-bottom:3px}
input[type=text],input[type=number],input[type=password],select,textarea{width:100%;background:#111;border:1px solid #222;color:#ccc;padding:5px 7px;font:12px/1.4 'Courier New',monospace;border-radius:3px;outline:none;resize:vertical}
input:focus,textarea:focus,select:focus{border-color:#444}
textarea{min-height:54px}
.row{display:flex;gap:8px}
.row>*{flex:1}
.check-row{display:flex;align-items:center;gap:8px;margin-top:4px}
.check-row input{width:auto}
.check-row span{font-size:12px;color:#999}
/* Personas */
.persona-card{background:#111;border:1px solid #1e1e1e;border-radius:4px;padding:10px;margin-bottom:8px}
.persona-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.persona-header span{color:#00ff41;font-size:12px}
.rm-btn{background:none;border:none;color:#555;cursor:pointer;font-size:14px;padding:0 4px}
.rm-btn:hover{color:#ff4444}
/* Buttons */
btn,button{font:12px 'Courier New',monospace;border-radius:3px;cursor:pointer;padding:6px 14px;border:1px solid #333;background:#1a1a1a;color:#ccc}
button:hover{background:#222;color:#eee}
.btn-save{border-color:#00ff4155;color:#00ff41}
.btn-save:hover{background:#00ff4115}
.btn-start{border-color:#00ff4155;color:#00ff41}
.btn-start:hover{background:#00ff4115}
.btn-stop{border-color:#ff444455;color:#ff4444}
.btn-stop:hover{background:#ff444415}
.btn-add{border-color:#3a3a3a;color:#888;font-size:11px;width:100%;margin-top:4px}
.controls{display:flex;gap:8px;margin-top:6px}
/* Log */
.log-header{padding:8px 14px;border-bottom:1px solid #1a1a1a;display:flex;align-items:center;gap:10px;flex-shrink:0}
.log-header h2{border:none;margin:0;padding:0}
#log{flex:1;overflow-y:auto;padding:10px 14px;font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-all}
.log-system{color:#444;font-style:italic}
.log-ok{color:#00ff41}
.log-err{color:#ff4444}
footer{background:#111;border-top:1px solid #1a1a1a;padding:5px 14px;font-size:10px;color:#333;flex-shrink:0;display:flex;justify-content:space-between}
#autoscroll-label{cursor:pointer;color:#444;user-select:none}
#autoscroll-label:hover{color:#666}
#autoscroll-label input{margin-right:4px}
</style>
</head>
<body>
<header>
  <h1>TRINITY CORE</h1>
  <div class="dot" id="dot-mnn" title="MNN Chat :8080"></div>
  <span class="label" id="lbl-mnn">MNN</span>
  <div class="dot" id="dot-eng" title="Engine"></div>
  <span class="label" id="lbl-eng">ENGINE</span>
  <span style="margin-left:auto;font-size:11px;color:#333" id="hdr-task"></span>
</header>

<div class="main">
<!-- ── LEFT: config ── -->
<div class="left panel" id="config-panel">

<section>
  <h2>General</h2>
  <label>Task</label>
  <input type="text" id="f-task" placeholder="task description">
  <div class="row" style="margin-top:8px">
    <div><label>Rounds</label><input type="number" id="f-rounds" min="1" value="10" style="width:80px"></div>
    <div><label>Log file</label><input type="text" id="f-logfile" placeholder="logs/trinity.md"></div>
  </div>
  <div class="check-row" style="margin-top:8px">
    <input type="checkbox" id="f-blake3" checked>
    <span>BLAKE3 hash-chaining</span>
  </div>
</section>

<section>
  <h2>LLM</h2>
  <div class="row">
    <div><label>URL</label><input type="text" id="f-llm-url" value="http://127.0.0.1:8080"></div>
    <div><label>Temp</label><input type="number" id="f-llm-temp" min="0" max="2" step="0.05" value="0.4" style="width:70px"></div>
    <div><label>Max tokens</label><input type="number" id="f-llm-tokens" min="50" value="300" style="width:80px"></div>
  </div>
  <label style="margin-top:8px">Model</label>
  <input type="text" id="f-llm-model" placeholder="model name">
</section>

<section>
  <h2>Input Source</h2>
  <label>Type</label>
  <select id="f-src-type" onchange="toggleSrcFields()">
    <option value="static">static</option>
    <option value="cisa_json">cisa_json</option>
  </select>
  <div id="src-url-row" style="display:none;margin-top:8px">
    <label>URL</label>
    <input type="text" id="f-src-url">
  </div>
  <div id="src-items-row" style="margin-top:8px">
    <label>Items (one per line)</label>
    <textarea id="f-src-items" rows="4"></textarea>
  </div>
</section>

<section>
  <h2>Personas</h2>
  <div id="personas-list"></div>
  <button class="btn-add" onclick="addPersona()">+ Add Persona</button>
</section>

<section>
  <h2>API Key</h2>
  <label>MNN_API_KEY (~/.termux_secrets)</label>
  <input type="password" id="f-apikey" placeholder="leave empty if not needed">
  <button style="margin-top:6px" onclick="saveApiKey()">Save Key</button>
</section>

<div class="controls">
  <button class="btn-save" onclick="saveConfig()">Save Config</button>
  <button class="btn-start" id="btn-engine" onclick="toggleEngine()">Start Engine</button>
</div>
<div style="margin-top:8px">
  <label>Config file to run</label>
  <input type="text" id="f-cfgpath" value="" placeholder="leave empty for default config.json">
</div>

</div><!-- /left -->

<!-- ── RIGHT: log ── -->
<div class="right">
  <div class="log-header">
    <h2>LIVE LOG</h2>
    <span id="log-lines" style="font-size:10px;color:#333">0 lines</span>
    <label id="autoscroll-label" style="margin-left:auto">
      <input type="checkbox" id="autoscroll" checked>auto-scroll
    </label>
  </div>
  <div id="log"></div>
</div>
</div><!-- /main -->

<footer>
  <span>TrinityCore UI</span>
  <span id="footer-status">idle</span>
</footer>

<script>
const $ = id => document.getElementById(id);
let lineCount = 0;
let engineRunning = false;

// ── Config load/save ──────────────────────────────────────────────────────────

async function loadConfig() {
  const r = await fetch('/api/config');
  if (!r.ok) return;
  const c = await r.json();
  $('f-task').value      = c.task || '';
  $('f-rounds').value    = c.rounds || 10;
  $('f-logfile').value   = c.log_file || 'logs/trinity.md';
  $('f-blake3').checked  = c.log_blake3 !== false;
  $('hdr-task').textContent = c.task || '';
  if (c.llm) {
    $('f-llm-url').value    = c.llm.url    || 'http://127.0.0.1:8080';
    $('f-llm-model').value  = c.llm.model  || '';
    $('f-llm-temp').value   = c.llm.temperature ?? 0.4;
    $('f-llm-tokens').value = c.llm.max_tokens  || 300;
  }
  const src = c.input_source || {};
  $('f-src-type').value = src.type || 'static';
  toggleSrcFields();
  if (src.url)   $('f-src-url').value = src.url;
  const items = src.items || src.fallback || [];
  $('f-src-items').value = items.join('\n');
  // personas
  $('personas-list').innerHTML = '';
  (c.personas || []).forEach(p => addPersona(p));
  // cfgpath default
  if (!$('f-cfgpath').value) $('f-cfgpath').value = '/data/data/com.termux/files/home/NeoBild/trinity_core/config.json';
}

function buildConfig() {
  const src_type = $('f-src-type').value;
  const items = $('f-src-items').value.split('\n').map(s=>s.trim()).filter(Boolean);
  const src = src_type === 'cisa_json'
    ? { type: 'cisa_json', url: $('f-src-url').value, fallback: items }
    : { type: 'static', items };
  const personas = [...document.querySelectorAll('.persona-card')].map(card => ({
    name:          card.querySelector('.p-name').value,
    role:          card.querySelector('.p-role').value,
    system_prompt: card.querySelector('.p-prompt').value,
  }));
  return {
    task:         $('f-task').value,
    rounds:       parseInt($('f-rounds').value) || 10,
    log_file:     $('f-logfile').value || 'logs/trinity.md',
    log_blake3:   $('f-blake3').checked,
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

async function saveConfig() {
  const cfg = buildConfig();
  $('hdr-task').textContent = cfg.task;
  const r = await fetch('/api/config', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg) });
  const d = await r.json();
  appendLog(d.ok ? '[config saved]' : '[save failed]', d.ok ? 'log-ok' : 'log-err');
  $('footer-status').textContent = d.ok ? 'config saved' : 'save error';
}

// ── Personas ──────────────────────────────────────────────────────────────────

function addPersona(p = {}) {
  const card = document.createElement('div');
  card.className = 'persona-card';
  card.innerHTML = `
    <div class="persona-header">
      <span>PERSONA</span>
      <button class="rm-btn" onclick="this.closest('.persona-card').remove()">✕</button>
    </div>
    <div class="row">
      <div><label>Name</label><input class="p-name" type="text" value="${esc(p.name||'')}"></div>
      <div><label>Role</label><input class="p-role" type="text" value="${esc(p.role||'')}"></div>
    </div>
    <label style="margin-top:6px">System Prompt</label>
    <textarea class="p-prompt">${esc(p.system_prompt||'')}</textarea>`;
  $('personas-list').appendChild(card);
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ── Input source toggle ───────────────────────────────────────────────────────

function toggleSrcFields() {
  const isCisa = $('f-src-type').value === 'cisa_json';
  $('src-url-row').style.display   = isCisa ? 'block' : 'none';
  $('src-items-row').querySelector('label').textContent = isCisa ? 'Fallback items (one per line)' : 'Items (one per line)';
}

// ── API key ───────────────────────────────────────────────────────────────────

async function loadApiKey() {
  const r = await fetch('/api/apikey');
  const d = await r.json();
  $('f-apikey').value = d.value || '';
}

async function saveApiKey() {
  await fetch('/api/apikey', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({value: $('f-apikey').value}) });
  appendLog('[API key saved]', 'log-ok');
}

// ── Engine start/stop ─────────────────────────────────────────────────────────

async function toggleEngine() {
  if (engineRunning) {
    await fetch('/api/stop', { method:'POST' });
    appendLog('[stop requested]', 'log-system');
  } else {
    const cfgpath = $('f-cfgpath').value.trim();
    await fetch('/api/start', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({config: cfgpath || undefined}) });
    appendLog('[engine starting...]', 'log-ok');
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
              : t.includes('Chain:') || t.includes('[chain]') ? 'log-ok'
              : '';
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
    const r = await fetch('/api/status');
    const d = await r.json();
    // MNN
    $('dot-mnn').className = 'dot ' + (d.mnn ? 'green' : 'red');
    $('lbl-mnn').textContent = 'MNN ' + (d.mnn ? '●' : '○');
    // Engine
    engineRunning = d.engine;
    $('dot-eng').className = 'dot ' + (d.engine ? 'green' : 'red');
    $('lbl-eng').textContent = 'ENGINE ' + (d.engine ? '●' : '○');
    $('btn-engine').textContent  = d.engine ? 'Stop Engine' : 'Start Engine';
    $('btn-engine').className    = d.engine ? 'btn-stop' : 'btn-start';
    $('footer-status').textContent = d.engine ? 'running' : 'idle';
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
