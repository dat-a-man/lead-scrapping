#!/usr/bin/env python3
"""Find the most senior data people at each lead company using Apify LinkedIn Company Employees scraper.

Batches all companies into a single Apify actor run to minimize start costs.
Output: one row per company with the best-scoring job + up to 3 contacts sorted by seniority.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Any

from apify_client import ApifyClient

ENV_PATH = Path(__file__).resolve().parent.parent / "scrape_leads" / ".env"

MAX_CONTACTS = 3

SENIORITY_RANK = {
    "cxo": 6, "chief": 6, "cdo": 6,
    "vp": 5, "vice president": 5,
    "head of": 4, "director": 4,
    "principal": 3, "staff": 3,
    "manager": 2, "lead": 2,
    "senior": 1,
}


def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def clean_linkedin_url(url: str) -> str:
    url = url.strip()
    if "?" in url:
        url = url.split("?")[0]
    url = url.rstrip("/")
    if url.startswith("https://de.linkedin.com/company/"):
        url = url.replace("https://de.linkedin.com/company/", "https://www.linkedin.com/company/")
    return url


def rank_person(headline: str) -> int:
    h = headline.lower()
    best = 0
    for keyword, rank in SENIORITY_RANK.items():
        if keyword in h:
            best = max(best, rank)
    is_cto = any(w in h for w in ["cto", "chief technology", "chief technical"])
    data_related = any(w in h for w in ["data", "analytics", "pipeline", "etl", "ingestion", "daten"])
    if not data_related and not is_cto:
        best = 0
    return best


def parse_contact(item: dict[str, Any]) -> dict[str, Any]:
    first = item.get("firstName", "")
    last = item.get("lastName", "")
    linkedin = item.get("linkedinUrl", "")
    emails = item.get("emails", [])
    email = emails[0] if emails else ""

    positions = item.get("currentPositions") or []
    title = positions[0].get("title", "") if positions else ""
    headline = item.get("headline", "") or title

    meta_companies = item.get("_meta", {}).get("query", {}).get("currentCompanies", [])
    source_company = clean_linkedin_url(meta_companies[0]) if meta_companies else ""

    return {
        "name": f"{first} {last}".strip(),
        "headline": headline,
        "linkedin": linkedin,
        "email": email,
        "sourceCompany": source_company,
        "seniorityRank": rank_person(headline),
    }


def find_contacts_for_company(
    client: ApifyClient, company_url: str, max_results: int = 3,
) -> list[dict[str, Any]]:
    """Search for senior data people at one company. Falls back to CTO if none found."""
    run = client.actor("harvestapi/linkedin-company-employees").call(run_input={
        "companies": [company_url],
        "searchQuery": "Data",
        "seniorityLevelIds": ["200", "210", "220", "300", "310"],
        "maxItems": max_results,
        "profileScraperMode": "Short ($4 per 1k)",
    })

    items = list(client.dataset(run.default_dataset_id).iterate_items())
    contacts = [parse_contact(item) for item in items]
    has_relevant = any(c["seniorityRank"] > 0 for c in contacts)

    if not has_relevant:
        run = client.actor("harvestapi/linkedin-company-employees").call(run_input={
            "companies": [company_url],
            "searchQuery": "CTO",
            "seniorityLevelIds": ["200", "210", "310"],
            "maxItems": max_results,
            "profileScraperMode": "Short ($4 per 1k)",
        })
        items = list(client.dataset(run.default_dataset_id).iterate_items())
        contacts = [parse_contact(item) for item in items]

    contacts.sort(key=lambda c: c["seniorityRank"], reverse=True)
    return contacts[:max_results]


def dedupe_companies(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """One row per company — keep the job with the highest fit_score."""
    best: dict[str, dict[str, str]] = {}
    for row in rows:
        url = row.get("companyLinkedinUrl", "").strip()
        if not url:
            continue
        clean = clean_linkedin_url(url)
        try:
            score = int(row.get("fit_score", 0))
        except (ValueError, TypeError):
            score = 0
        existing = best.get(clean)
        if existing is None:
            best[clean] = row
        else:
            try:
                existing_score = int(existing.get("fit_score", 0))
            except (ValueError, TypeError):
                existing_score = 0
            if score > existing_score:
                best[clean] = row
    return best


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find the most senior data people at each lead company."
    )
    parser.add_argument(
        "--input",
        default="data_scrapped/Germany/dlthub_leads.translated.csv",
        help="Input leads CSV file.",
    )
    parser.add_argument(
        "--output",
        default="data_scrapped/Germany/dlthub_leads_with_contacts.csv",
        help="Output CSV file.",
    )
    parser.add_argument(
        "--max-companies",
        type=int,
        default=10000,
        help="Max number of companies to process.",
    )
    parser.add_argument(
        "--results-per-company",
        type=int,
        default=3,
        help="Max contacts per company.",
    )
    args = parser.parse_args()

    load_env(ENV_PATH)

    api_token = os.environ.get("APIFY_API_TOKEN")
    if not api_token:
        print("ERROR: APIFY_API_TOKEN not found in .env", file=sys.stderr)
        return 1

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: file not found: {input_path}", file=sys.stderr)
        return 1

    with input_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Loaded {len(rows)} leads from {input_path}")

    companies = dedupe_companies(rows)
    company_urls = list(companies.keys())
    print(f"Deduped to {len(company_urls)} unique companies")

    client = ApifyClient(api_token)

    company_fields = [
        "companyName", "companyWebsite", "companyLinkedinUrl",
        "jobTitle", "location", "postedAt", "link",
        "fit_score", "fit_reason",
    ]
    contact_fields = []
    for i in range(1, MAX_CONTACTS + 1):
        contact_fields.extend([
            f"contact{i}_name",
            f"contact{i}_headline",
            f"contact{i}_linkedin",
            f"contact{i}_email",
        ])
    output_fieldnames = company_fields + contact_fields

    output_rows = []
    found = 0
    processed = 0

    for company_url, row in companies.items():
        if processed >= args.max_companies:
            break

        slug = company_url.split("/company/")[-1] if "/company/" in company_url else company_url
        print(f"  [{processed+1}/{min(len(companies), args.max_companies)}] {slug}...", end=" ", flush=True)

        contacts = []
        try:
            contacts = find_contacts_for_company(client, company_url, args.results_per_company)
            if contacts and any(c["seniorityRank"] > 0 for c in contacts):
                best = contacts[0]
                print(f"→ {best['name']} ({best['headline'][:50]})")
                found += 1
            else:
                print("→ no data contacts found")
                contacts = []
        except Exception as e:
            print(f"→ ERROR: {e}")

        out_row = {k: row.get(k, "") for k in company_fields}
        for i in range(MAX_CONTACTS):
            idx = i + 1
            if i < len(contacts):
                c = contacts[i]
                out_row[f"contact{idx}_name"] = c["name"]
                out_row[f"contact{idx}_headline"] = c["headline"]
                out_row[f"contact{idx}_linkedin"] = c["linkedin"]
                out_row[f"contact{idx}_email"] = c["email"]
            else:
                out_row[f"contact{idx}_name"] = ""
                out_row[f"contact{idx}_headline"] = ""
                out_row[f"contact{idx}_linkedin"] = ""
                out_row[f"contact{idx}_email"] = ""

        output_rows.append(out_row)
        processed += 1

    print(f"\nFound contacts for {found}/{processed} companies")

    output_rows.sort(key=lambda r: int(r.get("fit_score", 0) or 0), reverse=True)

    output_path = Path(args.output)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=output_fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
