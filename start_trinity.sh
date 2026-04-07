#!/data/data/com.termux/files/usr/bin/bash
# Launches llama-server + Trinity Orchestrator.
# Keeps both alive overnight — auto-restarts on crash.

MODEL="$HOME/models/llama-3.2-3b-instruct-q4_k_m.gguf"
LLAMA_BIN="/data/data/com.termux/files/usr/bin/llama-server"
SCRIPT_DIR="$HOME/NeoBild"
LOG_DIR="$SCRIPT_DIR/logs"

mkdir -p "$LOG_DIR"

DATE=$(date +%Y%m%d_%H%M%S)
LLAMA_LOG="$LOG_DIR/llama_server_$DATE.log"

# ── Sanity checks ───────────────────────────────────────────────────────────
if [ ! -f "$MODEL" ]; then
  echo "FEHLER: Modell nicht gefunden: $MODEL"
  exit 1
fi
if [ ! -x "$LLAMA_BIN" ]; then
  echo "FEHLER: llama-server nicht gefunden oder nicht ausführbar: $LLAMA_BIN"
  exit 1
fi

# ── llama-server control ────────────────────────────────────────────────────
LLAMA_PID=""

start_llama() {
  echo "[$(date '+%H:%M:%S')] Starte llama-server..."
  "$LLAMA_BIN" \
    --model        "$MODEL" \
    --host         127.0.0.1 \
    --port         8080 \
    --ctx-size     4096 \
    --threads      4 \
    --n-predict    512 \
    --log-disable \
    > "$LLAMA_LOG" 2>&1 &
  LLAMA_PID=$!
  echo "[$(date '+%H:%M:%S')] llama-server PID=$LLAMA_PID, log=$LLAMA_LOG"
}

wait_llama() {
  echo "[$(date '+%H:%M:%S')] Warte auf llama-server (port 8080)..."
  for i in $(seq 1 60); do
    if curl -s http://127.0.0.1:8080/health > /dev/null 2>&1; then
      echo "[$(date '+%H:%M:%S')] llama-server bereit (${i}x2s)."
      return 0
    fi
    sleep 2
  done
  echo "[$(date '+%H:%M:%S')] llama-server nicht bereit nach 120s."
  return 1
}

llama_alive() {
  [ -n "$LLAMA_PID" ] && kill -0 "$LLAMA_PID" 2>/dev/null
}

# ── Cleanup on exit ─────────────────────────────────────────────────────────
cleanup() {
  echo ""
  echo "[$(date '+%H:%M:%S')] Beende Trinity & llama-server..."
  [ -n "$LLAMA_PID" ] && kill "$LLAMA_PID" 2>/dev/null
  exit 0
}
trap cleanup INT TERM

# ── Main watchdog loop ───────────────────────────────────────────────────────
echo "════════════════════════════════════════════════"
echo "  Trinity Overnight Runner"
echo "  Modell : $MODEL"
echo "  Logs   : $LOG_DIR"
echo "  Ctrl+C zum Beenden"
echo "════════════════════════════════════════════════"

TRINITY_CRASHES=0

while true; do
  # Ensure llama-server is running
  if ! llama_alive || ! curl -s http://127.0.0.1:8080/health > /dev/null 2>&1; then
    [ -n "$LLAMA_PID" ] && kill "$LLAMA_PID" 2>/dev/null
    start_llama
    if ! wait_llama; then
      echo "[$(date '+%H:%M:%S')] llama-server Start fehlgeschlagen. Retry in 30s..."
      sleep 30
      continue
    fi
  fi

  # Run orchestrator
  echo "[$(date '+%H:%M:%S')] Starte trinity_orchestrator.py (Crash #$TRINITY_CRASHES bisher)..."
  cd "$SCRIPT_DIR"
  python3 trinity_orchestrator.py
  EXIT_CODE=$?

  TRINITY_CRASHES=$((TRINITY_CRASHES + 1))
  echo "[$(date '+%H:%M:%S')] Orchestrator beendet (exit=$EXIT_CODE, Crash #$TRINITY_CRASHES)."

  # exit 2 = server gone, give llama time to recover
  if [ "$EXIT_CODE" -eq 2 ]; then
    echo "[$(date '+%H:%M:%S')] Server-Verlust erkannt. Warte 15s vor Neustart..."
    sleep 15
  else
    sleep 5
  fi
done
