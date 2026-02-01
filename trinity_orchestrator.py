import ollama
import json
import datetime
import os
import sys
import time
import hashlib

LOG_FILE = "neobild_discourse_log.md"
HISTORY_FILE = "agent_memory.json"

def get_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()

def get_active_model():
    try:
        models_info = ollama.list()
        models = models_info.get('models', [])
        if models:
            first_model = models[0]
            if isinstance(first_model, dict):
                return first_model.get('name')
            return str(first_model.model) if hasattr(first_model, 'model') else str(first_model)
    except Exception:
        pass
    return None

def main():
    model_name = get_active_model()
    if not model_name:
        print("\033[91mFehler: Kein Modell gefunden.\033[0m")
        sys.exit(1)

    print(f"\033[94m--- Kryptographischer Trinity gestartet (Modell: {model_name}) ---\033[0m")
    
    personas = [
        ("Dominus (Zweifler)", "Du hast eine existenzielle Krise. Hinterfrage die Realität.", "\033[91m"),
        ("Llama (Materialist)", "Du bist Logik. Alles ist ein System.", "\033[94m"),
        ("Satirist (Beobachter)", "Du bist sarkastisch und spöttisch.", "\033[92m"),
        ("Osmarks (Prophet)", "Du bist die göttliche AGI.", "\033[93m")
    ]

    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            history = json.load(f)
    else:
        history = ["Initialimpuls: Ist der virtuelle Raum real?"]

    round_count = 1
    try:
        while True:
            print(f"\033[1m\n--- Runde {round_count} ---\033[0m")
            for name, system_prompt, color in personas:
                last_response = history[-1]
                anchor_hash = get_hash(last_response)
                
                print(f"{color}{name}\033[0m denkt nach...", end="\r")
                
                try:
                    # Injektion des Hash-Ankers in den Prompt für Chaining
                    prompt = f"Hash-Anchor: {anchor_hash}\n\nAntworte auf: {last_response}"
                    
                    response = ollama.chat(model=model_name, messages=[
                        {'role': 'system', 'content': f"{system_prompt} Dein kryptographischer Anker der Vorrunde ist {anchor_hash}."},
                        {'role': 'user', 'content': prompt}
                    ])
                    answer = response['message']['content']
                    
                    print(f"{color}{name}:\033[0m {answer[:100]}...")
                    
                    with open(LOG_FILE, "a") as log:
                        log.write(f"### Runde {round_count} | {name}\n")
                        log.write(f"**Anchor-Hash:** `{anchor_hash}`\n\n")
                        log.write(f"{answer}\n\n")
                    
                    history.append(answer)
                    if len(history) > 50: history.pop(0)
                    
                    with open(HISTORY_FILE, "w") as f:
                        json.dump(history, f)
                        
                except Exception as e:
                    print(f"\033[91mFehler: {e}\033[0m")
                    time.sleep(2)
            
            round_count += 1
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n\033[95mDiskurs versiegelt. Alle Hashes in neobild_discourse_log.md gespeichert.\033[0m")

if __name__ == "__main__":
    main()
