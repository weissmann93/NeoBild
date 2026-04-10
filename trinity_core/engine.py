#!/usr/bin/env python3
"""TrinityCore — universal multi-agent loop engine. Config-driven, BLAKE3-chained."""
import json, os, re, time, random, datetime, requests, blake3

CFG     = os.path.join(os.path.dirname(__file__), "config.json")
SECRETS = os.path.expanduser("~/.termux_secrets")

def _read_secret(key):
    if not os.path.exists(SECRETS):
        return ""
    with open(SECRETS) as f:
        for line in f:
            k, _, v = line.strip().partition("=")
            if k == key:
                return v
    return ""

# Inject MNN_API_KEY into env from secrets file if not already set
_api_key = os.environ.get("MNN_API_KEY") or _read_secret("MNN_API_KEY")
if _api_key:
    os.environ["MNN_API_KEY"] = _api_key
CHAIN = os.path.join(os.path.dirname(__file__), "hashes", "chain.jsonl")
GENESIS = "0" * 64

# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path=CFG):
    with open(path) as f:
        return json.load(f)

# ── Logging ───────────────────────────────────────────────────────────────────

def ts():
    return datetime.datetime.now().strftime("%H:%M:%S")

def logline(msg, log_fh):
    line = f"[{ts()}] {msg}"
    print(line, flush=True)
    log_fh.write(line + "\n")
    log_fh.flush()

# ── BLAKE3 chain ──────────────────────────────────────────────────────────────

def _h(text):
    return blake3.blake3(text.encode()).hexdigest()

def chain_tail():
    if not os.path.exists(CHAIN):
        return GENESIS
    last = None
    with open(CHAIN) as f:
        for line in f:
            line = line.strip()
            if line:
                last = line
    return json.loads(last)["hash"] if last else GENESIS

def chain_append(entry):
    os.makedirs(os.path.dirname(CHAIN), exist_ok=True)
    prev = chain_tail()
    h    = _h(entry + prev)
    with open(CHAIN, "a") as f:
        f.write(json.dumps({"entry": entry, "hash": h, "prev_hash": prev}) + "\n")
    return h

def verify_chain():
    if not os.path.exists(CHAIN):
        return True, None
    prev = GENESIS
    with open(CHAIN) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r["prev_hash"] != prev or r["hash"] != _h(r["entry"] + r["prev_hash"]):
                return False, i
            prev = r["hash"]
    return True, None

# ── LLM ──────────────────────────────────────────────────────────────────────

def chat(llm_cfg, system_prompt, user_content):
    for attempt in range(3):
        try:
            r = requests.post(
                f"{llm_cfg['url']}/v1/chat/completions",
                json={"model": llm_cfg["model"],
                      "messages": [{"role": "system", "content": system_prompt},
                                   {"role": "user",   "content": user_content}],
                      "temperature": llm_cfg.get("temperature", 0.4),
                      "max_tokens":  llm_cfg.get("max_tokens", 300),
                      "stream": True},
                headers={"Authorization": f"Bearer {os.environ.get('MNN_API_KEY', '')}"},
                timeout=180, stream=True,
            )
            r.raise_for_status()
            chunks = []
            for raw in r.iter_lines():
                if not raw:
                    continue
                line = raw.decode() if isinstance(raw, bytes) else raw
                if not line.startswith("data: ") or line[6:].strip() == "[DONE]":
                    continue
                try:
                    delta = json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                    if delta:
                        chunks.append(delta)
                        sys.stdout.write(delta)
                        sys.stdout.flush()
                except (KeyError, json.JSONDecodeError):
                    pass
            print(flush=True)
            answer = re.sub(r'<think>.*?</think>', '', "".join(chunks), flags=re.DOTALL).strip()
            return answer or None
        except Exception as e:
            print(f"  [retry {attempt+1}] {e}", flush=True)
            time.sleep(8)
    return None

# ── Input sources ─────────────────────────────────────────────────────────────

def fetch_topics(src):
    kind = src.get("type", "static")
    if kind == "static":
        return src["items"]
    if kind == "cisa_json":
        try:
            data = requests.get(src["url"], timeout=15).json()
            vulns = data["vulnerabilities"]
            selected = random.sample(vulns, min(20, len(vulns)))
            return [
                f"Analyze: {v.get('cveID','')} – {v.get('vendorProject','')} "
                f"{v.get('product','')}: {v.get('vulnerabilityName','')}. "
                f"{v.get('shortDescription','')}"
                for v in selected
            ]
        except Exception as e:
            print(f"[input] fetch failed ({e}), falling back to static", flush=True)
            return src.get("fallback", ["Analyze a recent critical CVE."])
    raise ValueError(f"Unknown input_source type: {kind}")

# ── Main loop ─────────────────────────────────────────────────────────────────

def run(cfg_path=CFG):
    cfg      = load_config(cfg_path)
    llm      = cfg["llm"]
    personas = cfg["personas"]
    rounds   = cfg.get("rounds", 10)
    log_path = os.path.join(os.path.dirname(__file__), cfg.get("log_file", "logs/trinity.md"))
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    with open(log_path, "a") as log_fh:
        logline(f"=== TrinityCore: {cfg['task']} ===", log_fh)

        ok, broken = verify_chain()
        logline(f"[chain] {'OK' if ok else f'BROKEN at entry {broken}!'}", log_fh)

        topics = fetch_topics(cfg["input_source"])
        logline(f"[input] {len(topics)} topics loaded", log_fh)

        for rnd in range(1, rounds + 1):
            topic   = topics[rnd % len(topics)]
            prev    = topic
            ts_iso  = datetime.datetime.now().isoformat(timespec="seconds")
            logline(f"--- Round {rnd} ---", log_fh)
            log_fh.write(f"\n## Round {rnd} | {ts_iso}\n\n**Topic:** {topic}\n\n")

            for p in personas:
                tail = chain_tail()
                print(f"\n\033[90m[{ts()}] Chain: {tail[:12]}...\033[0m", flush=True)
                print(f"\033[96m{p['name']}:\033[0m ", end="", flush=True)

                answer = chat(llm, p["system_prompt"], prev)
                if not answer:
                    logline(f"  {p['name']}: NO ANSWER", log_fh)
                    continue

                entry_hash = chain_append(f"Round {rnd} | {p['name']} | {ts_iso} | {answer}")
                log_fh.write(f"### {p['name']} ({p['role']})\n")
                log_fh.write(f"**BLAKE3:** `{entry_hash}`\n\n{answer}\n\n")
                log_fh.flush()
                logline(f"  {p['name']}: ok", log_fh)
                prev = answer

            time.sleep(2)

    print("\n[TrinityCore] done.", flush=True)

if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else CFG)
