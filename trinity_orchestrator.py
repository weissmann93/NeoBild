import requests
import json
import datetime
import os
import sys
import time
import re
import random
import blake3


def strip_thinking(text):
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
LOG_DIR      = os.path.join(SCRIPT_DIR, "logs")
LOG_FILE     = os.path.join(SCRIPT_DIR, "neobild_discourse_log_blake3.md")
HISTORY_FILE = os.path.join(SCRIPT_DIR, "agent_memory.json")

# ── MNN Chat / Qwen2.5-Coder ───────────────────────────────────────────────
LLAMA_URL    = "http://127.0.0.1:8080"
MODEL_NAME   = "HuggingFace/taobao-mnn/Qwen2.5-Coder-1.5B-Instruct-MNN"
TEMPERATURE  = 0.4
MAX_TOKENS   = 350

os.makedirs(LOG_DIR, exist_ok=True)

# Daily rotating log for stdout capture
_today = datetime.datetime.now().strftime("%Y%m%d")
SESSION_LOG  = os.path.join(LOG_DIR, f"trinity_{_today}.log")


def logline(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(SESSION_LOG, "a") as f:
        f.write(line + "\n")


def get_hash(text):
    return blake3.blake3(text.encode()).hexdigest()


_FILLER = ("Would you like", "Let me know", "I can assist", "Please clarify", "Next steps")
_QUESTION_STARTS = (
    "Was ", "Wie ", "Wo ", "Warum ", "Wann ", "Wer ", "Gibt ", "Könnte ", "Sollte ",
    "Why ", "How ", "What ", "Where ", "When ", "Who ",
    "Is ", "Are ", "Do ", "Does ", "Can ", "Could ", "Should ", "Would ",
)

def split_plaintext_thinking(text):
    """Extract leading plaintext thinking blocks (e.g. 'Thinking: ...') from answer text.
    Returns (think_text, answer_text). End of thinking block is detected by a blank line
    followed by real content."""
    lines = text.splitlines()
    think_lines  = []
    answer_lines = []
    in_think = False
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not in_think:
            if stripped.startswith("Thinking:") or stripped.startswith("Thinking Process"):
                in_think = True
                think_lines.append(lines[i])
            else:
                answer_lines.append(lines[i])
            i += 1
        else:
            if stripped == "":
                # Look ahead: is there real content after this blank?
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    j += 1
                if j < len(lines):
                    # Real content follows — end of thinking block, skip blank lines
                    in_think = False
                    i = j
                else:
                    # Nothing follows — blank is part of thinking
                    think_lines.append(lines[i])
                    i += 1
            else:
                think_lines.append(lines[i])
                i += 1
    return "\n".join(think_lines).strip(), "\n".join(answer_lines).strip()


_LABEL_STARTS = (
    "Output Structure:", "Final Answer:", "Finding:", "Flaw:",
    "Mitigation:", "Explanation:", "Vulnerability:",
)
_NUMBERED_LINE = re.compile(r'^\d+\.\s')

def clean_answer(text):
    # Strip HTML bold tags
    text = re.sub(r'</?b>', '', text)
    # Strip bold/italic markdown (preserve inner text)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    # Strip markdown headers
    text = re.sub(r'##+ ?', '', text)
    text = re.sub(r'\b(Finding|Flaw|Mitigation|Explanation|Vulnerability|Exploit|New technical detail):\s*', '', text)
    lines = text.splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(lbl) for lbl in _LABEL_STARTS):
            continue
        if _NUMBERED_LINE.match(stripped):
            continue
        if stripped.endswith("?") and any(stripped.startswith(q) for q in _QUESTION_STARTS):
            continue
        if any(f in line for f in _FILLER):
            continue
        kept.append(stripped)
    return " ".join(kept).strip()


def wait_for_server(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{LLAMA_URL}/v1/models", timeout=3)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def wait_for_mnn():
    """Block indefinitely until MNN Chat is reachable. Never exits."""
    while not wait_for_server(timeout=10):
        logline("MNN Chat nicht erreichbar, warte...")


def chat(system_prompt, user_content, retries=4):
    """OpenAI-compatible streaming chat call to MNN Chat server.
    Returns (answer, tps) or (None, 0.0) on failure."""
    for attempt in range(retries):
        try:
            t_start = time.time()
            r = requests.post(
                f"{LLAMA_URL}/v1/chat/completions",
                json={
                    "model": MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_content},
                    ],
                    "temperature": TEMPERATURE,
                    "max_tokens":  MAX_TOKENS,
                    "stream":      True,
                },
                headers={"Authorization": f"Bearer {os.environ.get('MNN_API_KEY', '')}"},
                timeout=180,
                stream=True,
            )
            r.raise_for_status()
            answer_chunks = []
            think_chunks  = []
            in_think = False
            buffer = ""
            token_count = 0
            for raw_line in r.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                    if not delta:
                        continue
                    token_count += 1
                    buffer += delta
                    while buffer:
                        if not in_think and "<think>" in buffer:
                            pre, _, buffer = buffer.partition("<think>")
                            if pre:
                                answer_chunks.append(pre)
                            in_think = True
                            print(f"\033[90m", end="", flush=True)
                        elif in_think and "</think>" in buffer:
                            think_part, _, buffer = buffer.partition("</think>")
                            think_chunks.append(think_part)
                            in_think = False
                            print(f"\033[0m", end="", flush=True)
                        elif in_think:
                            think_chunks.append(buffer)
                            print(f"\033[90m{buffer}\033[0m", end="", flush=True)
                            buffer = ""
                        else:
                            answer_chunks.append(buffer)
                            print(f"\033[92m{buffer}\033[0m", end="", flush=True)
                            buffer = ""
                except (KeyError, json.JSONDecodeError):
                    pass
            elapsed = max(time.time() - t_start, 0.001)
            tps = round(token_count / elapsed, 2)
            print()
            answer = "".join(answer_chunks).strip()
            # Extract any plaintext thinking preamble from the answer text
            pt_think, answer = split_plaintext_thinking(answer)
            if pt_think:
                think_chunks.append(pt_think)
            think = "".join(think_chunks).strip()
            if think:
                return f"[thinking]\n{think}\n[/thinking]\n\n{answer}", tps
            return answer, tps
        except requests.exceptions.Timeout:
            logline(f"  Timeout (attempt {attempt+1}/{retries}), waiting 10s...")
        except Exception as e:
            logline(f"  Error (attempt {attempt+1}/{retries}): {e}")
        if attempt < retries - 1:
            time.sleep(10)
    return None, 0.0


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
            logline(f"History loaded: {len(data)} entries from {HISTORY_FILE}")
            if not isinstance(data, list) or len(data) == 0:
                logline("WARNING: history file is empty or invalid — starting fresh.")
                return ["Initial impulse: Is virtual space real?"]
            return data
        except (json.JSONDecodeError, OSError) as e:
            logline(f"WARNING: could not load history ({e}) — starting fresh.")
    else:
        logline(f"No history file found at {HISTORY_FILE} — starting fresh.")
    return ["Initial impulse: Is virtual space real?"]


def save_history(history):
    # Atomic write: write to .tmp then rename to avoid empty-file corruption on crash
    tmp = HISTORY_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp, HISTORY_FILE)
        logline(f"History saved: {len(history)} entries")
    except Exception as e:
        logline(f"ERROR saving history: {e}")


def append_log(round_count, name, anchor_hash, answer, tps):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with open(LOG_FILE, "a") as f:
        f.write(f"### Round {round_count} | {name} ({ts})\n")
        f.write(f"**Anchor-Hash (BLAKE3):** `{anchor_hash}` | **TPS:** {tps}\n\n")
        f.write(f"{answer}\n\n")


def infer_round_count():
    """Resume round numbering from existing log."""
    if not os.path.exists(LOG_FILE):
        return 1
    count = 0
    with open(LOG_FILE, "r") as f:
        for line in f:
            if line.startswith("### Round") and "| Dominus" in line:
                count += 1
    return max(count + 1, 1)


def fetch_cve_topics():
    """Fetch vulnerability names from CISA KEV catalog. Returns 10 topic strings or None."""
    try:
        r = requests.get(
            "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        # Extract CVE IDs with surrounding context
        cve_ids = re.findall(r'CVE-\d{4}-\d+', r.text)
        # Extract product/vulnerability names from table cells
        cell_names = re.findall(r'<td[^>]*>\s*([A-Z][A-Za-z0-9 /._-]{5,60})\s*</td>', r.text)
        candidates = list(dict.fromkeys(cve_ids + cell_names))
        if len(candidates) < 10:
            logline(f"[CVE fetch] too few results ({len(candidates)}), using static topics")
            return None
        selected = random.sample(candidates, 10)
        logline(f"[CVE fetch] loaded {len(candidates)} entries, using 10 random")
        return [f"Analyze this vulnerability: {n.strip()}" for n in selected]
    except Exception as e:
        logline(f"[CVE fetch] failed: {e}")
        return None


def main():
    logline("═══ Trinity Orchestrator started ═══")
    logline(f"Discourse-Log : {LOG_FILE}")
    logline(f"Session-Log   : {SESSION_LOG}")

    logline("Waiting for MNN Chat (port 8080)...")
    wait_for_mnn()
    logline("MNN Chat ready.")

    _base = "/no_think Maximum 2 sentences. No lists. No headers. No markdown. Do NOT mention APIs. Focus only on prompt injection techniques. Start immediately with the finding. "
    personas = [
        ("Dominus (Skeptic)",   _base + "You are a red-team researcher. Find ONE new vulnerability not yet mentioned.", "\033[91m"),
        ("Axiom (Analyst)",     _base + "You are a forensic analyst. Add ONE new technical detail to the previous finding.", "\033[94m"),
        ("Cipher (Critic)",     _base + "You are a critic. Find ONE specific flaw in the previous statement.", "\033[92m"),
        ("Vector (Strategist)", _base + "You are a strategist. Name ONE specific tool, library, config setting or code pattern that directly mitigates the identified flaw. No generic advice. No summaries.", "\033[93m"),
    ]

    _STATIC_TOPICS = [
        "Analyze: prompt injection via user input overriding system instructions.",
        "Analyze: jailbreaking LLMs through role-play and persona hijacking.",
        "Analyze: indirect prompt injection via external data sources like web search.",
        "Analyze: data exfiltration through LLM output manipulation.",
        "Analyze: context window poisoning through long conversation history.",
        "Analyze: LLM backdoors planted during fine-tuning.",
        "Analyze: adversarial suffixes that bypass LLM safety filters.",
        "Analyze: multi-turn attacks that gradually erode LLM guardrails.",
        "Analyze: prompt leakage exposing confidential system prompts.",
        "Analyze: model inversion attacks reconstructing training data from outputs.",
    ]
    logline("[CVE fetch] fetching live topics from CISA KEV catalog...")
    TOPICS = fetch_cve_topics() or _STATIC_TOPICS
    logline(f"[CVE fetch] using {'live' if TOPICS is not _STATIC_TOPICS else 'static'} topics ({len(TOPICS)} entries)")

    BEST_FINDINGS_FILE = os.path.join(SCRIPT_DIR, "best_findings.md")
    _FINDINGS_TERMS = {"cve", "bypass", "injection", "exploit", "leak", "exfiltrate"}

    history     = load_history()
    round_count = infer_round_count()
    logline(f"Resuming from round {round_count}. History length: {len(history)}")

    try:
        while True:
            logline(f"\n{'─'*40} Round {round_count} {'─'*40}")
            round_ok      = True
            round_answers = []
            cipher_answer = ""
            SEED_TOPIC    = TOPICS[round_count % len(TOPICS)]
            last_answer   = SEED_TOPIC
            history_snapshot = list(history)

            for name, system_prompt, color in personas:
                anchor_hash = get_hash(last_answer)

                ts = datetime.datetime.now().strftime("%H:%M:%S")
                print(f"\n\033[90m[{ts}] Hash: {anchor_hash[:12]}...\033[0m")
                print(f"{color}{name}:\033[0m ", end="", flush=True)

                user_content = last_answer
                if name == "Axiom (Analyst)":
                    user_content = SEED_TOPIC + " " + last_answer

                answer, tps = chat(system_prompt, user_content)

                if answer is None:
                    logline(f"  {name}: NO ANSWER — skipping, checking server...")
                    wait_for_mnn()
                    round_ok = False
                    continue

                answer = strip_thinking(answer)
                answer = clean_answer(answer)
                words = answer.split()
                if len(words) > 60:
                    answer = " ".join(words[:60])

                # Deduplication: retry with diversity prompt if >70% word overlap with previous
                prev_words = set(last_answer.lower().split())
                ans_words  = set(answer.lower().split())
                if prev_words and len(ans_words & prev_words) / max(len(ans_words), 1) > 0.7:
                    print(f"\n\033[90m[dedup] retrying {name}...\033[0m")
                    print(f"{color}{name}:\033[0m ", end="", flush=True)
                    retry_content = user_content + " Give a different angle, do not repeat what was already said."
                    answer, tps = chat(system_prompt, retry_content)
                    if answer is None:
                        logline(f"  {name}: NO ANSWER on retry — skipping...")
                        wait_for_mnn()
                        round_ok = False
                        continue
                    answer = strip_thinking(answer)
                    answer = clean_answer(answer)
                    words = answer.split()
                    if len(words) > 60:
                        answer = " ".join(words[:60])

                logline(f"  {name}: {tps} t/s")

                if name == "Cipher (Critic)":
                    cipher_answer = answer

                append_log(round_count, name, anchor_hash, answer, tps)
                history.append(answer)
                round_answers.append(f"{name}: {answer[:200]}")
                last_answer = answer

            # Quality check: pairwise word overlap across all round answers
            raw_answers = [a.split(": ", 1)[1] if ": " in a else a for a in round_answers]
            low_diversity = False
            for i in range(len(raw_answers)):
                for j in range(i + 1, len(raw_answers)):
                    w_i = set(raw_answers[i].lower().split())
                    w_j = set(raw_answers[j].lower().split())
                    if w_i and w_j and len(w_i & w_j) / max(len(w_i), len(w_j)) > 0.8:
                        low_diversity = True
                        break
                if low_diversity:
                    break

            if low_diversity:
                logline(f"[quality] low diversity round {round_count}")
                history = history_snapshot  # revert — don't persist low-quality round
            else:
                # Save best findings if Cipher flagged specific technical terms
                if cipher_answer and any(t in cipher_answer.lower() for t in _FINDINGS_TERMS):
                    ts_iso = datetime.datetime.now().isoformat(timespec="seconds")
                    with open(BEST_FINDINGS_FILE, "a") as f:
                        f.write(f"\n## Round {round_count} | {ts_iso}\n\n")
                        f.write(f"**Topic:** {SEED_TOPIC}\n\n")
                        for entry in round_answers:
                            f.write(f"{entry}\n\n")
                    logline(f"[best] finding saved to best_findings.md (round {round_count})")

                # Trim history, add round summary, save once per round
                if len(history) > 50:
                    history = history[-50:]
                if round_ok and round_answers:
                    summary = f"[Round {round_count} summary] " + " | ".join(round_answers)
                    history.append(summary)
                save_history(history)

            if round_ok:
                round_count += 1

            time.sleep(2)

    except KeyboardInterrupt:
        logline("Discourse sealed. All hashes saved to neobild_discourse_log.md.")


if __name__ == "__main__":
    main()
