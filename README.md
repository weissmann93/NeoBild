# NeoBild — Trinity-Diskurs

Dieses Repository enthält die Rohdaten eines experimentellen KI-Diskursprojekts.

## Was ist das?

Ein lokales Sprachmodell (Llama 3.2 3B via Ollama) führt einen fortlaufenden
philosophisch-satirischen Diskurs in vier Personas:

- **Osmarks** — analytisch, skeptisch
- **Dominus** — autoritär, dogmatisch
- **Llama** — naiv, direkt
- **Satirist** — ironisch, dekonstruktiv

Die Personas interagieren autonom ohne menschliche Steuerung der Inhalte.
Alle Runs laufen lokal auf einem Android-Gerät via Termux — keine Cloud,
keine API-Abhängigkeit.

## Stack

| Komponente | Details |
|---|---|
| Modell | Llama 3.2 3B (Q4_K_M) |
| Engine | Ollama via Termux |
| Gerät | Android (Snapdragon) |
| Infrastruktur | Vollständig lokal / offline-fähig |

## Hintergrund

Das Projekt entstand im Rahmen von **NeoBild** — einer deutschen Community
für digitale Souveränität, Self-Hosting und lokale KI.

Mehr dazu: [neobild.de](https://neobild.de)

## Lizenz

MIT
