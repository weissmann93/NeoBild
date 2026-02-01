#!/bin/bash

# Verzeichnisse erstellen
mkdir -p archive_raw
mkdir -p hashes

# Dateinamen festlegen
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RAW_FILE="archive_raw/raw_$TIMESTAMP.md"
HASH_FILE="hashes/raw_$TIMESTAMP.sha256"

# Aktuellen Stand kopieren
cp neobild_discourse_log.md latest_discourse.md
cp neobild_discourse_log.md "$RAW_FILE"

# Hashwert erzeugen (SHA-256)
sha256sum "$RAW_FILE" > "$HASH_FILE"

echo "--------------------------------------"
echo "Rohdaten gesichert: $RAW_FILE"
echo "Hashwert generiert: $HASH_FILE"
echo "Status: Bereit für GitHub-Push"
echo "--------------------------------------"
