#!/bin/bash
# run_full_import.sh
#
# Splits a huge Django fixture into small chunks (avoids the OOM kill
# that happened on the direct 2.2GB import), then imports every chunk
# in order, stopping immediately if any chunk fails.
#
# USAGE (from the project root, e.g. ~/uni_server/uiversity_timetabling):
#   bash run_full_import.sh ./exports/timetabling_export_20260822_171804.json
#
set -e

INPUT_FILE="$1"
if [ -z "$INPUT_FILE" ]; then
  echo "Usage: bash run_full_import.sh <path-to-huge-fixture.json>"
  exit 1
fi
if [ ! -f "$INPUT_FILE" ]; then
  echo "ERROR: file not found: $INPUT_FILE"
  exit 1
fi

SPLIT_DIR="./exports/split"
SPLITTER="./split_fixture_internal.py"

# ── Write the tested splitter script ─────────────────────────────────
cat > "$SPLITTER" << 'PYEOF'
#!/usr/bin/env python3
import argparse, json, os, sys

CHUNK_READ_BYTES = 8 * 1024 * 1024

def skip_ws_and_comma(buf, pos):
    n = len(buf)
    while pos < n and buf[pos] in " \t\r\n":
        pos += 1
    if pos < n and buf[pos] == ",":
        pos += 1
        while pos < n and buf[pos] in " \t\r\n":
            pos += 1
    return pos

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output_dir")
    ap.add_argument("--batch-size", type=int, default=1000)
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"ERROR: no such file: {args.input}")
    os.makedirs(args.output_dir, exist_ok=True)

    decoder = json.JSONDecoder()
    buf = ""
    pos = 0
    started = False
    finished = False
    total_records = 0
    chunk_index = 0
    batch = []

    def flush_batch():
        nonlocal chunk_index, batch
        if not batch:
            return
        chunk_index += 1
        out_path = os.path.join(args.output_dir, f"chunk_{chunk_index:04d}.json")
        with open(out_path, "w", encoding="utf-8") as out:
            json.dump(batch, out)
        print(f"  wrote {out_path}  ({len(batch)} records)")
        batch = []

    with open(args.input, "r", encoding="utf-8") as fh:
        while True:
            if pos > 0:
                buf = buf[pos:]
                pos = 0

            if not started:
                stripped = buf.lstrip()
                if stripped.startswith("["):
                    buf = stripped[1:]
                    started = True
                else:
                    chunk = fh.read(CHUNK_READ_BYTES)
                    if not chunk:
                        sys.exit("ERROR: reached end of file before finding opening '['.")
                    buf += chunk
                    continue

            progressed = True
            while progressed:
                progressed = False
                pos = skip_ws_and_comma(buf, pos)
                remainder = buf[pos:].lstrip()
                if remainder.startswith("]"):
                    finished = True
                    break
                try:
                    obj, end = decoder.raw_decode(buf, pos)
                except (ValueError, json.JSONDecodeError):
                    break
                pos = end
                total_records += 1
                batch.append(obj)
                if len(batch) >= args.batch_size:
                    flush_batch()
                progressed = True

            if finished:
                break

            chunk = fh.read(CHUNK_READ_BYTES)
            if not chunk:
                sys.exit("ERROR: ran out of file before finding closing ']'.")
            buf = buf[pos:] + chunk
            pos = 0

    flush_batch()
    print()
    print(f"TOTAL_RECORDS_SPLIT={total_records}")
    print(f"CHUNK_FILES_WRITTEN={chunk_index}")

if __name__ == "__main__":
    main()
PYEOF

# ── Step 1: split ─────────────────────────────────────────────────────
echo "=========================================="
echo "STEP 1/3: Splitting $INPUT_FILE into $SPLIT_DIR"
echo "=========================================="
rm -rf "$SPLIT_DIR"
python3 "$SPLITTER" "$INPUT_FILE" "$SPLIT_DIR" --batch-size 1000
echo ""
echo "Split complete."
echo ""

# ── Step 2: backup ────────────────────────────────────────────────────
echo "=========================================="
echo "STEP 2/3: Backing up the database"
echo "=========================================="
mkdir -p ~/backups
BACKUP_FILE=~/backups/pilot_db_before_import_$(date +%Y%m%d_%H%M).sql
echo "You will be prompted for the DB root password (from your .env)."
sudo docker compose exec db mysqldump -u root -p pilot_db > "$BACKUP_FILE"
echo "Backup written to: $BACKUP_FILE"
ls -lh "$BACKUP_FILE"
echo ""

# ── Step 3: import every chunk, in order, stop on first failure ──────
echo "=========================================="
echo "STEP 3/3: Importing chunks"
echo "=========================================="
CHUNK_LIST=("$SPLIT_DIR"/chunk_*.json)
TOTAL=${#CHUNK_LIST[@]}
COUNT=0
FAILED=0

for f in "${CHUNK_LIST[@]}"; do
  COUNT=$((COUNT + 1))
  echo ""
  echo "=== [$COUNT/$TOTAL] Importing $f ==="
  if ./docker/import_data.sh "$f"; then
    echo "=== [$COUNT/$TOTAL] OK ==="
  else
    echo ""
    echo "!!! FAILED on chunk $COUNT ($f) — stopping here. !!!"
    echo "!!! Chunks 1 through $((COUNT - 1)) already imported successfully. !!!"
    FAILED=1
    break
  fi
done

echo ""
echo "=========================================="
if [ "$FAILED" -eq 1 ]; then
  echo "STOPPED EARLY at chunk $COUNT of $TOTAL. Fix the error above, then re-run"
  echo "the import loop manually starting from chunk $COUNT, or investigate before retrying."
else
  echo "ALL $TOTAL CHUNKS IMPORTED."
fi
echo "Rollback point (if needed): $BACKUP_FILE"
echo "=========================================="
