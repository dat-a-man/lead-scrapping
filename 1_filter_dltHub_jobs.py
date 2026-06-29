#!/usr/bin/env python3
# coding: utf-8
"""Filter LinkedIn job scrape results for dltHub-style Data Engineering leads."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
    AI_AVAILABLE = True
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]
    AI_AVAILABLE = False

ENV_PATH = Path(__file__).resolve().parent.parent / "scrape_leads" / ".env"

TITLE_PATTERN = re.compile(
    r"\b(data|analytics\s+engineer|etl|elt)\b",
    re.I,
)

DLTHUB_TEXT_PATTERN = re.compile(
    r"\b(fivetran|airbyte|stitch|meltano|singer|dbt|dagster|prefect|airflow|"
    r"etl|elt|batch\s+processing|batch\s+pipeline|data\s+pipeline|data\s+pipelines|"
    r"data\s+ingestion|data\s+integration|data\s+warehouse|data\s+lake|"
    r"data\s+platform|data\s+infrastructure|data\s+orchestration|"
    r"extract\s+transform\s+load|load\s+transform|"
    r"snowflake|bigquery|redshift|databricks|delta\s+lake|iceberg|"
    r"python.{0,20}pipeline|pipeline.{0,20}python|"
    r"daten\s*pipeline|daten\s*integration|daten\s*ingestion)\b",
    re.I,
)

HTML_TAG_RE = re.compile(r"<[^>]+>")


def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def strip_html(text: str) -> str:
    return HTML_TAG_RE.sub(" ", text or "")


def create_ai_client() -> Any:
    if not AI_AVAILABLE:
        raise RuntimeError(
            "The 'openai' package is required for --use-ai. Install it with 'pip install openai'."
        )
    api_key = os.environ.get("GPT_API_KEY")
    if not api_key:
        raise RuntimeError("GPT_API_KEY not found in environment or .env file.")
    return OpenAI(api_key=api_key)


DLTHUB_CONTEXT = (
    "dltHub is an agent-native data engineering platform for building, running, and "
    "operating production-grade data pipelines. It is built on the open-source library dlt "
    "(data load tool). Key capabilities:\n"
    "- Ingestion: extract-and-load pipelines from REST APIs, SQL databases, cloud storage, "
    "with schema inference, normalization, and incremental loading\n"
    "- Transformations: Python-decorated transformations, dbt integration, SQL transformations\n"
    "- Data quality & governance: declarative correctness rules, schema drift control, tests\n"
    "- Pipeline operations: deploy, schedule, monitor pipelines without managing infrastructure\n"
    "- Agent-native workflow: designed for coding agents (Claude Code, Codex, Cursor)\n"
    "- Competes with / replaces: Fivetran, Airbyte, Stitch, Meltano, custom ETL/ELT scripts\n"
    "- Python-based, open-source core, managed cloud platform\n\n"
    "A good dltHub lead is a company that builds or maintains data pipelines, does ETL/ELT, "
    "data ingestion, data integration, or uses tools dltHub replaces (Fivetran, Airbyte, etc.). "
    "NOT a good lead: pure analytics/BI roles with no pipeline work, ML/AI-only roles, "
    "recruitment agencies, or roles focused solely on dashboards/reporting."
)


def classify_job_with_ai(
    client: Any,
    model: str,
    title: str,
    company_description: str,
    description_text: str,
    description_html: str,
) -> dict[str, Any]:
    prompt = (
        "You are a sales intelligence analyst for dltHub.\n\n"
        f"## What is dltHub\n{DLTHUB_CONTEXT}\n\n"
        "## Job posting\n"
        f"Title: {title.strip()}\n\n"
        f"Company description: {company_description.strip()[:2000]}\n\n"
        f"Job description: {description_text.strip()[:5000]}\n\n"
        "## Task\n"
        "Read the job description in its original language (German, English, etc.).\n"
        "Score how good this company is as a sales lead for dltHub on a 1-5 scale:\n"
        "  5 = Hot lead: explicitly mentions tools dltHub replaces (Fivetran, Airbyte, "
        "Stitch, Meltano) or building custom ETL/ELT pipelines in Python\n"
        "  4 = Strong lead: building/maintaining data pipelines, data ingestion, "
        "data integration as core responsibilities\n"
        "  3 = Moderate lead: data engineering role with some pipeline work but "
        "mixed with analytics/ML/other duties\n"
        "  2 = Weak lead: mentions data but focused on analytics, BI, dashboards, "
        "or ML/AI with no clear pipeline work\n"
        "  1 = Not a lead: no relevance to data pipelines or data engineering\n\n"
        "Return ONLY a JSON object:\n"
        '  "fit_score" (1-5)\n'
        '  "reason" — one sentence explaining the score\n'
    )

    response = client.chat.completions.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {
            "fit_score": 1,
            "reason": "failed to parse AI response",
        }

    try:
        score = int(result.get("fit_score", 1))
    except (ValueError, TypeError):
        score = 1

    return {
        "fit_score": max(1, min(5, score)),
        "reason": str(result.get("reason", "")).strip(),
    }



def matches_title(title: str) -> bool:
    return bool(TITLE_PATTERN.search(title or ""))



def description_matches_dltHub(description: str) -> bool:
    return bool(DLTHUB_TEXT_PATTERN.search(strip_html(description) if description else ""))


def normalize_link(url: str) -> str:
    return url.strip() if url else ""


def filter_jobs(
    records: list[dict[str, Any]],
    ai_client: Any | None = None,
    ai_model: str | None = None,
    ai_max: int = 0,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    ai_count = 0

    for record in records:
        title = (record.get("title") or "").strip()
        company_description = (record.get("companyDescription") or "").strip()
        description_text = (record.get("descriptionText") or "").strip()
        description_html = (record.get("descriptionHtml") or "").strip()
        description_html_text = strip_html(description_html)
        description_combined = "\n".join(filter(None, [description_text, description_html_text]))

        if not matches_title(title):
            continue

        ai_reviewed = False
        fit_score = 0
        fit_reason = ""

        if ai_client is not None and ai_count < ai_max:
            ai_result = classify_job_with_ai(
                ai_client,
                ai_model or "gpt-4o-mini",
                title,
                company_description,
                description_text,
                description_html,
            )
            ai_count += 1
            ai_reviewed = True
            fit_score = ai_result["fit_score"]
            fit_reason = ai_result["reason"]

            if ai_count % 50 == 0:
                print(f"  AI processed: {ai_count} jobs...", flush=True)

            if fit_score < 3:
                continue
        else:
            if not description_matches_dltHub(description_combined):
                continue

        filtered.append(
            {
                "companyName": record.get("companyName", ""),
                "companyWebsite": normalize_link(record.get("companyWebsite", "")),
                "companyLinkedinUrl": normalize_link(record.get("companyLinkedinUrl", "")),
                "companyDescription": company_description,
                "jobTitle": title,
                "location": record.get("location", ""),
                "postedAt": record.get("postedAt", ""),
                "link": normalize_link(record.get("link", "")),
                "descriptionText": description_text,
                "descriptionHtml": description_html,
                "seniorityLevel": record.get("seniorityLevel", ""),
                "employmentType": record.get("employmentType", ""),
                "industries": record.get("industries", ""),
                "ai_reviewed": ai_reviewed,
                "fit_score": fit_score,
                "fit_reason": fit_reason,
            }
        )

    return filtered


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    fieldnames = [
        "companyName",
        "companyWebsite",
        "companyLinkedinUrl",
        "jobTitle",
        "location",
        "postedAt",
        "link",
        "descriptionText",
        "companyDescription",
        "seniorityLevel",
        "employmentType",
        "fit_score",
        "fit_reason",
        "industries",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in records:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Filter LinkedIn job postings for dltHub-style Data Engineering roles."
    )
    parser.add_argument(
        "--input",
        default="data_scrapped/dataset_linkedin-jobs-scraper_2026-06-25_11-54-29-011.json",
        help="Path to the LinkedIn scrape JSON file.",
    )
    parser.add_argument(
        "--output-csv",
        default="data_scrapped/dataset_linkedin-jobs-scraper_2026-06-25_11-54-29-011.filtered.csv",
        help="Output CSV file path.",
    )
    parser.add_argument(
        "--output-json",
        default="data_scrapped/dataset_linkedin-jobs-scraper_2026-06-25_11-54-29-011.filtered.json",
        help="Output JSON file path.",
    )
    parser.add_argument(
        "--use-ai",
        action="store_true",
        help="Use Claude (Anthropic) to confirm recruiter status and batch/ETL relevance for candidate jobs.",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="OpenAI model to use when --use-ai is enabled.",
    )
    parser.add_argument(
        "--ai-max",
        type=int,
        default=200,
        help="Maximum number of candidate records to validate with AI when --use-ai is enabled.",
    )
    args = parser.parse_args()

    load_env(ENV_PATH)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    records: list[dict[str, Any]]
    with input_path.open("r", encoding="utf-8") as f:
        records = json.load(f)

    ai_client = None
    if args.use_ai:
        ai_client = create_ai_client()

    filtered = filter_jobs(
        records,
        ai_client=ai_client,
        ai_model=args.model,
        ai_max=args.ai_max,
    )
    print(f"Found {len(filtered)} candidate jobs from {len(records)} total records.")

    output_csv = Path(args.output_csv)
    write_csv(filtered, output_csv)
    print(f"Wrote CSV to {output_csv}")

    output_json = Path(args.output_json)
    with output_json.open("w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)
    print(f"Wrote JSON to {output_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
