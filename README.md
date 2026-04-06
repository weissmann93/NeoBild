# NeoBild — Trinity Multi-Agent Security Orchestrator

An autonomous 4-agent red-team dialog loop running entirely on Android via Termux — no cloud, no API dependency, fully local and offline-capable.

## Agents

| Persona | Role |
|---|---|
| **Dominus (Skeptic)** | Red-team researcher — finds new vulnerabilities not yet mentioned |
| **Axiom (Analyst)** | Forensic analyst — adds one new technical detail to the previous finding |
| **Cipher (Critic)** | Critic — identifies one specific flaw in the previous statement |
| **Vector (Strategist)** | Strategist — names one specific tool, library or config that mitigates the flaw |

The agents react to each other in a chained loop: each persona receives the previous persona's output as input, grounding every response in the prior finding.

## Stack

| Component | Details |
|---|---|
| Model | Qwen2.5-Coder-1.5B-Instruct (MNN quantized) |
| Engine | MNN Chat server via Termux (OpenAI-compatible API on port 8080) |
| Device | Redmi Note 14 Pro+ (Snapdragon 7s Gen 3, 8 GB RAM) |
| Infrastructure | Fully local / offline |

## Features

- **Live CVE topic fetching** — pulls vulnerability names from the [CISA Known Exploited Vulnerabilities catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) at startup; falls back to a curated static topic list if the fetch fails
- **Rotating topics** — each round cycles through 10 topics (CVE-sourced or static) so the discourse never repeats the same seed twice
- **Chained agent reactions** — Axiom, Cipher and Vector each receive the previous agent's answer as input, producing a coherent 4-step analysis per round
- **Diversity / quality check** — after each round, pairwise word-overlap across all 4 answers is computed; rounds with >80% overlap are discarded and not written to history
- **Best findings extractor** — if Cipher's answer contains high-signal terms (`CVE`, `bypass`, `injection`, `exploit`, `leak`, `exfiltrate`), the full round dialog is appended to `best_findings.md` with timestamp and topic
- **Hash-anchored discourse log** — every response is written to `neobild_discourse_log.md` with a SHA-256 anchor hash for traceability
- **Thinking-mode support** — `<think>...</think>` blocks stream in dark gray and are stripped from stored answers; `/no_think` is passed to suppress thinking on models that support it
- **Automatic deduplication** — if a response has >70% word overlap with the previous one, the agent is retried with a diversity prompt

## Files

| File | Purpose |
|---|---|
| `trinity_orchestrator.py` | Main agent loop — fetches topics, runs personas, saves findings |
| `trinity_viewer.py` | Terminal viewer for live discourse output |
| `best_findings.md` | Auto-extracted high-signal findings (gitignored) |
| `neobild_discourse_log.md` | Full hash-anchored round log (gitignored) |
| `agent_memory.json` | Sliding history window (gitignored) |

## Usage

Start MNN Chat on port 8080, then:

```bash
python3 ~/NeoBild/trinity_orchestrator.py
```

Or use the alias defined in `~/.bashrc`:

```bash
trinity
```

## Background

This project is part of **NeoBild** — a German community for digital sovereignty, self-hosting and local AI.

More at: [neobild.de](https://neobild.de)

## License

MIT
