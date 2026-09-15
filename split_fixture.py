#!/usr/bin/env python3
"""
split_fixture.py — split a huge Django fixture (JSON list of
{model, pk, fields} objects) into smaller fixture files, WITHOUT ever
loading the whole file into memory.

Why this exists: a plain `json.load()` on a multi-GB fixture (plus the
import command's own duplicate in-memory list) can use several times
the file's size in actual RAM, and get killed by the OOM killer with no
traceback — exactly what happened with the 2.2GB export on this box.

This script instead keeps only a small rolling text buffer in memory
(a few MB at most) and repeatedly asks Python's C-implemented JSON
decoder to parse "the next one object" out of that buffer, using
json.JSONDecoder().raw_decode() — which happily parses one value and
tells you where it stopped, ignoring whatever comes after. That lets us
walk the file one record at a time, no matter how large the file is.

USAGE
  python3 split_fixture.py INPUT.json OUTPUT_DIR [--batch-size N]

  e.g.
  python3 split_fixture.py ./exports/timetabling_export_20260822_171804.json ./exports/split --batch-size 1000

Produces OUTPUT_DIR/chunk_0001.json, chunk_0002.json, ... — each a
normal, small, standalone JSON fixture (a JSON list), safe to import
one at a time with the existing docker/import_data.sh script.

After it finishes, it prints the total record count — compare this
against the "Total records in file" line from `import_data --dry-run`
on the original file, to confirm nothing was lost in the split.
"""
import argparse
import json
import os
import sys

CHUNK_READ_BYTES = 8 * 1024 * 1024  # read the source file 8MB at a time


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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="Path to the huge fixture JSON file")
    ap.add_argument("output_dir", help="Directory to write chunk_XXXX.json files into")
    ap.add_argument("--batch-size", type=int, default=1000,
                     help="Records per output chunk file (default: 1000)")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"ERROR: no such file: {args.input}")

    os.makedirs(args.output_dir, exist_ok=True)

    decoder = json.JSONDecoder()
    buf = ""
    pos = 0
    started = False   # have we consumed the opening '[' yet?
    finished = False  # have we consumed the closing ']' yet?
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
            # Compact the buffer: drop everything already consumed.
            if pos > 0:
                buf = buf[pos:]
                pos = 0

            if not started:
                # Find and consume the opening '[' of the top-level array.
                stripped = buf.lstrip()
                if stripped.startswith("["):
                    buf = stripped[1:]
                    started = True
                else:
                    chunk = fh.read(CHUNK_READ_BYTES)
                    if not chunk:
                        sys.exit("ERROR: reached end of file before finding opening '['. "
                                 "Is this really a JSON-list fixture?")
                    buf += chunk
                    continue

            # Try to consume as many complete objects as are already
            # sitting in the buffer before reading more from disk.
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
                    # Not enough data yet to parse the next object —
                    # break out and read more from the file, UNLESS
                    # we're already at end of file, in which case this
                    # is a genuine parse error.
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
                sys.exit(
                    "ERROR: ran out of file before finding closing ']'. "
                    "The file may be truncated or not valid JSON."
                )
            buf = buf[pos:] + chunk
            pos = 0

    flush_batch()

    print()
    print(f"Done. Total records split: {total_records}")
    print(f"Chunk files written: {chunk_index}")
    print(f"Compare {total_records} against the 'Total records in file' line")
    print("from `import_data --dry-run` on the original file to confirm nothing was lost.")


if __name__ == "__main__":
    main()
