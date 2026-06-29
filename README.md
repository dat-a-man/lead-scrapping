# dltHub Lead Scraping Pipeline

A 3-step pipeline that scrapes LinkedIn job postings, identifies companies that are a good fit for [dltHub](https://dlthub.com), and finds the senior-most data decision-makers at those companies.

## How It Works

```
LinkedIn Jobs (Apify)
    |
    v
1_filter_dltHub_jobs.py  ──>  Qualified leads (CSV + JSON)
    |
    v
2_translate_leads.py     ──>  Translated leads (optional)
    |
    v
3_find_contacts.py       ──>  Leads + senior data contacts (CSV)
```

## Prerequisites

### API Keys

Add these to a `.env` file:

```
GPT_API_KEY="sk-..."
APIFY_API_TOKEN="apify_api_..."
```

### Python Packages

```bash
pip install openai apify-client deep-translator
```

### Apify Actors

You need two Apify actors:

1. **[curious_coder/linkedin-jobs-scraper](https://apify.com/curious_coder/linkedin-jobs-scraper)** - Scrapes LinkedIn job postings
2. **[harvestapi/linkedin-company-employees](https://apify.com/harvestapi/linkedin-company-employees)** - Finds company employees by search query

## Step 0: Scrape LinkedIn Jobs

Use the **curious_coder/linkedin-jobs-scraper** actor on Apify to scrape job postings.

**Setup:**
- Enter the country name you want to scrape jobs for
- In the actor's settings, set **"Split by country"** to `true`
- Cost: ~$1 per 1,000 jobs searched

The actor outputs a JSON file with job postings including title, company info, description, and more. Download this JSON - it's the input for Step 1.

Example leads from a UK scrape are in `py/example_leads/UK/`.

## Step 1: Filter & Classify Leads

```bash
python3 1_filter_dltHub_jobs.py \
  --input <path-to-scraped-jobs.json> \
  --output-csv <output-leads.csv> \
  --output-json <output-leads.json> \
  --use-ai --ai-max 10000
```

**What it does:**

1. **Title regex filter** (free) - Keeps jobs with "data", "analytics engineer", "ETL", or "ELT" in the title. This cuts out irrelevant jobs before any API calls.

2. **GPT-4o mini classification** (with `--use-ai`) - For each job that passes the title filter, sends the job description to GPT-4o mini with context about what dltHub is. The AI scores each company as a lead on a 1-5 scale:
   - **5** = Hot lead: explicitly mentions tools dltHub replaces (Fivetran, Airbyte, Stitch, Meltano) or building custom ETL/ELT pipelines in Python
   - **4** = Strong lead: building/maintaining data pipelines, data ingestion, data integration as core responsibilities
   - **3** = Moderate lead: data engineering role with some pipeline work mixed with other duties
   - **2** = Weak lead: mentions data but focused on analytics/BI/dashboards with no clear pipeline work
   - **1** = Not a lead

   Jobs scoring below 3 are dropped.

**Without `--use-ai`:** Falls back to a keyword regex that checks descriptions for terms like Fivetran, Airbyte, Snowflake, data pipeline, ETL, etc. Cheaper but misses leads that don't use those exact words.

**Cost:** ~$0.40-0.50 per 1,000 jobs with GPT-4o mini.

**Output:** CSV and JSON with qualified leads including `fit_score` and `fit_reason`.

## Step 2: Translate Descriptions (Optional)

```bash
python3 2_translate_leads.py \
  --input <leads.csv>
```

Only needed if job descriptions are in a non-English language. Uses Google Translate via `deep-translator` (free, no API key required).

**What it does:**
- Reads the leads CSV from Step 1
- Detects non-English descriptions (currently tuned for German using word-frequency hints)
- Translates `descriptionText` and `companyDescription` columns to English
- Outputs `<input>.translated.csv` - original file is untouched

**Cost:** Free.

**When to skip:** For English-speaking countries (US, UK, etc.) this step is unnecessary.

## Step 3: Find Senior Data Contacts

```bash
python3 3_find_contacts.py \
  --input <leads.csv> \
  --output <leads_with_contacts.csv>
```

**What it does:**

1. **Deduplicates** companies from the leads CSV (many jobs may come from the same company). Keeps one row per company with the highest `fit_score`.

2. **Searches for senior data people** at each company using the `harvestapi/linkedin-company-employees` Apify actor:
   - Search query: `"Data"` with seniority filters (VP, Director, Manager, Senior, CXO)
   - Returns up to 3 contacts per company, sorted by seniority
   - Uses short/basic profile mode ($4 per 1,000 profiles)

3. **CTO fallback** - If no data-related contacts are found, runs a second search for `"CTO"` to find the tech decision-maker.

4. **Ranks contacts** by seniority: CXO/Chief > VP > Head/Director > Principal/Staff > Manager/Lead > Senior

**Email lookup:** The script uses short profile mode by default ($4/1k). The Apify actor also supports email discovery — change `profileScraperMode` in the script to find verified emails:

| Mode | Cost per 1,000 profiles | Returns |
|---|---|---|
| Short ($4 per 1k) | $4 | Name, headline, LinkedIn URL, current position |
| Full ($8 per 1k) | $8 | Full profile: work history, education, skills |
| Full + email search ($12 per 1k) | $12 | Full profile + validated email (SMTP verified) |

Note: Email search is not guaranteed to find an email for every profile. If a profile lacks sufficient information for email verification, you won't be charged for that search attempt.

**Cost:** ~$0.02 per company (actor start) + ~$0.003 per profile (short mode). With CTO fallback for ~40% of companies, expect roughly $0.03 per company total.

**Output:** CSV with one row per company, sorted by `fit_score` (hottest leads first):

| Company columns | Contact columns (x3) |
|---|---|
| companyName, companyWebsite, companyLinkedinUrl, jobTitle, location, fit_score, fit_reason | contact1_name, contact1_headline, contact1_linkedin, contact1_email, contact2_..., contact3_... |

All CSV outputs are formatted for direct import into Google Sheets (File > Import > Upload > select the CSV). Leads are sorted by `fit_score` so the hottest prospects are at the top.

## Cost Summary

| Step | Tool | Cost |
|---|---|---|
| Step 0 | Apify LinkedIn Jobs Scraper | ~$1 per 1,000 jobs |
| Step 1 | GPT-4o mini | ~$0.40 per 1,000 jobs classified |
| Step 2 | Google Translate | Free |
| Step 3 | Apify LinkedIn Employees | ~$0.03 per company |

**Example: 3,000 jobs scraped from Germany**
- Step 0: ~$3
- Step 1: ~$0.50 (932 jobs classified)
- Step 2: Free
- Step 3: ~$6.70 (223 unique companies)
- **Total: ~$10**

## Example Usage (Full Pipeline)

```bash
# 1. Classify leads
python3 1_filter_dltHub_jobs.py \
  --input data/Germany/linkedin_jobs.json \
  --output-csv data/Germany/dlthub_leads.csv \
  --output-json data/Germany/dlthub_leads.json \
  --use-ai --ai-max 10000

# 2. Translate (skip for English-speaking countries)
python3 2_translate_leads.py \
  --input data/Germany/dlthub_leads.csv

# 3. Find contacts
python3 3_find_contacts.py \
  --input data/Germany/dlthub_leads.translated.csv \
  --output data/Germany/dlthub_leads_with_contacts.csv
```
