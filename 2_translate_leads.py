#!/usr/bin/env python3
"""Translate non-English descriptionText and companyDescription to English using Google Translate (free)."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

from deep_translator import GoogleTranslator

GERMAN_HINTS = [
    " und ", " der ", " die ", " das ", " für ", " wir ", " sie ",
    " mit ", " von ", " nicht ", " ein ", " eine ", " im ", " zum ",
    " oder ", " als ", " sind ", " den ", " dem ", " des ",
]

TRANSLATOR = GoogleTranslator(source="auto", target="en")
MAX_CHUNK = 4500


def needs_translation(text: str) -> bool:
    if not text or len(text.strip()) < 20:
        return False
    sample = text[:600].lower()
    return sum(1 for h in GERMAN_HINTS if h in sample) >= 2


def translate_text(text: str) -> str:
    if not text or not text.strip():
        return text
    if len(text) <= MAX_CHUNK:
        return TRANSLATOR.translate(text)
    chunks = []
    start = 0
    while start < len(text):
        end = start + MAX_CHUNK
        if end < len(text):
            break_at = text.rfind("\n", start, end)
            if break_at == -1 or break_at <= start:
                break_at = text.rfind(". ", start, end)
            if break_at > start:
                end = break_at + 1
        chunks.append(text[start:end])
        start = end
    translated = []
    for chunk in chunks:
        translated.append(TRANSLATOR.translate(chunk))
        time.sleep(0.3)
    return "".join(translated)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Translate non-English columns in a leads CSV to English using Google Translate (free)."
    )
    parser.add_argument(
        "--input",
        default="data_scrapped/dlthub_leads.csv",
        help="Input CSV file.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV file. Defaults to <input>_translated.csv",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: file not found: {input_path}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else input_path.with_suffix(".translated.csv")

    with input_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    print(f"Loaded {len(rows)} rows from {input_path}")

    translated_desc = 0
    translated_company = 0

    for i, row in enumerate(rows):
        desc = row.get("descriptionText", "")
        comp = row.get("companyDescription", "")

        if needs_translation(desc):
            try:
                row["descriptionText"] = translate_text(desc)
                translated_desc += 1
            except Exception as e:
                print(f"  Row {i+1}: failed to translate descriptionText: {e}")

        if needs_translation(comp):
            try:
                row["companyDescription"] = translate_text(comp)
                translated_company += 1
            except Exception as e:
                print(f"  Row {i+1}: failed to translate companyDescription: {e}")

        if (i + 1) % 20 == 0:
            print(f"  Processed {i+1}/{len(rows)} rows...")

    print(f"\nTranslated {translated_desc} descriptionText, {translated_company} companyDescription")

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
