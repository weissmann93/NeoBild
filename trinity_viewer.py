import datetime
import os
import subprocess
import time

from flask import Flask, Response, render_template_string

app = Flask(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR    = os.path.join(SCRIPT_DIR, "logs")


def get_log_file():
    today = datetime.datetime.now().strftime("%Y%m%d")
    return os.path.join(LOG_DIR, f"trinity_{today}.log")


HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trinity Viewer</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0d0d0d;
      color: #c9c9c9;
      font-family: 'Courier New', Courier, monospace;
      font-size: 13px;
      display: flex;
      flex-direction: column;
      height: 100vh;
    }
    header {
      background: #111;
      border-bottom: 1px solid #222;
      padding: 10px 16px;
      display: flex;
      align-items: center;
      gap: 14px;
      flex-shrink: 0;
    }
    header h1 { font-size: 14px; color: #eee; letter-spacing: 2px; }
    #status {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 10px;
      background: #1a3a1a;
      color: #6bff8a;
    }
    #status.disconnected { background: #3a1a1a; color: #ff6b6b; }
    #logname { margin-left: auto; font-size: 11px; color: #444; }
    #log {
      flex: 1;
      overflow-y: auto;
      padding: 12px 16px;
      display: flex;
      flex-direction: column;
      gap: 1px;
    }
    .line {
      white-space: pre-wrap;
      word-break: break-all;
      line-height: 1.6;
      padding: 2px 0 2px 8px;
      border-left: 2px solid transparent;
    }
    .line-Dominus { border-left-color: #ff6b6b55; }
    .line-Axiom   { border-left-color: #6bb3ff55; }
    .line-Cipher  { border-left-color: #6bff8a55; }
    .line-Vector  { border-left-color: #ffd96b55; }
    .line-system  { color: #555; font-style: italic; border-left-color: #333; }
    .think-block {
      background: #1a1a1a;
      border-left: 2px solid #444;
      padding: 6px 10px;
      margin: 4px 0;
      font-size: 11px;
      font-style: italic;
      color: #666;
      white-space: pre-wrap;
      word-break: break-all;
      line-height: 1.5;
    }
    .think-label {
      font-size: 10px;
      color: #555;
      font-style: normal;
      display: block;
      margin-bottom: 4px;
    }
    .ts   { color: #3a3a3a; }
    .persona-Dominus { color: #ff6b6b; font-weight: bold; }
    .persona-Axiom   { color: #6bb3ff; font-weight: bold; }
    .persona-Cipher  { color: #6bff8a; font-weight: bold; }
    .persona-Vector  { color: #ffd96b; font-weight: bold; }
    footer {
      background: #111;
      border-top: 1px solid #222;
      padding: 6px 16px;
      font-size: 11px;
      color: #444;
      flex-shrink: 0;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    #autoscroll-label { cursor: pointer; color: #555; user-select: none; }
    #autoscroll-label:hover { color: #888; }
    #autoscroll-label input { margin-right: 4px; }
  </style>
</head>
<body>
  <header>
    <h1>TRINITY VIEWER</h1>
    <span id="status">CONNECTING</span>
    <span id="logname"></span>
  </header>
  <div id="log"></div>
  <footer>
    <span id="linecount">0 lines</span>
    <label id="autoscroll-label">
      <input type="checkbox" id="autoscroll" checked>auto-scroll
    </label>
  </footer>

  <script>
    const logEl      = document.getElementById('log');
    const statusEl   = document.getElementById('status');
    const linecountEl = document.getElementById('linecount');
    const autoscrollEl = document.getElementById('autoscroll');
    let count = 0;
    let inThinking = false;
    let thinkBuf   = [];

    const PERSONAS = ['Dominus', 'Axiom', 'Cipher', 'Vector'];

    function esc(s) {
      return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function detectPersona(text) {
      for (const p of PERSONAS) {
        if (text.includes(p)) return p;
      }
      return null;
    }

    function highlightLine(raw) {
      let s = esc(raw);
      s = s.replace(/^(\[\d{2}:\d{2}:\d{2}\])/, '<span class="ts">$1</span>');
      for (const p of PERSONAS) {
        const re = new RegExp(`(${p}(?:\\s*\\([^)]*\\))?)`, 'g');
        s = s.replace(re, `<span class="persona-${p}">$1</span>`);
      }
      return s;
    }

    function flushThinkBlock() {
      const div = document.createElement('div');
      div.className = 'think-block';
      div.innerHTML = '<span class="think-label">🧠 Thinking</span>' + esc(thinkBuf.join('\n'));
      logEl.appendChild(div);
      thinkBuf = [];
      inThinking = false;
      if (autoscrollEl.checked) logEl.scrollTop = logEl.scrollHeight;
    }

    function appendLine(text) {
      if (text === '[thinking]') {
        inThinking = true;
        thinkBuf = [];
        return;
      }
      if (text === '[/thinking]') {
        flushThinkBlock();
        return;
      }
      if (inThinking) {
        thinkBuf.push(text);
        return;
      }
      const persona = detectPersona(text);
      const div = document.createElement('div');
      div.className = 'line ' + (persona ? `line-${persona}` : 'line-system');
      div.innerHTML = highlightLine(text);
      logEl.appendChild(div);
      count++;
      linecountEl.textContent = count + ' lines';
      if (autoscrollEl.checked) {
        logEl.scrollTop = logEl.scrollHeight;
      }
    }

    function connect() {
      const es = new EventSource('/stream');

      es.onopen = () => {
        statusEl.textContent = 'LIVE';
        statusEl.className = '';
      };

      es.onmessage = e => {
        appendLine(e.data);
      };

      es.onerror = () => {
        statusEl.textContent = 'DISCONNECTED';
        statusEl.className = 'disconnected';
        es.close();
        setTimeout(connect, 3000);
      };
    }

    fetch('/logname')
      .then(r => r.text())
      .then(name => { document.getElementById('logname').textContent = name; });

    connect();
  </script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/logname")
def logname():
    return os.path.basename(get_log_file())


@app.route("/stream")
def stream():
    def generate():
        log_file = get_log_file()
        while not os.path.exists(log_file):
            yield f"data: [Waiting for {os.path.basename(log_file)}...]\n\n"
            time.sleep(2)

        proc = subprocess.Popen(
            ["tail", "-n", "200", "-f", log_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            for line in iter(proc.stdout.readline, b""):
                text = line.decode("utf-8", errors="replace").rstrip("\n\r")
                if text:
                    yield f"data: {text}\n\n"
        finally:
            proc.terminate()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
