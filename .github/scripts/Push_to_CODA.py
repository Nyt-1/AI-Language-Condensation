import os
import re
import html
import requests
from datetime import date, datetime
import time
import json
from dotenv import load_dotenv

load_dotenv()

SLACK_TOKEN = os.environ["SLACK_BOT_TOKEN"]
DEST_CHANNEL_ID = os.environ["DEST_CHANNEL_ID"]

CODA_TOKEN = os.environ["CODA_API_TOKEN"]
CODA_DOC_ID = os.environ["CODA_DOC_ID"]
CODA_TABLE_ID = os.environ["CODA_TABLE_ID"]
CODA_TIMELINE_TABLE_ID = os.environ["CODA_TIMELINE_TABLE_ID"]

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Team roster as JSON: {"Display Name": "work email", ...}. Keys are the valid
# owner/assignee names; values are used to set the Attio deal owner (may be "").
TEAM_MEMBERS = json.loads(os.environ.get("TEAM_MEMBERS") or "{}")
ANTHROPIC_MODEL = "claude-sonnet-4-6"

NOT_MENTIONED = ""
DEAL_FUNNEL_SOURCED = "Sourcing"
DEAL_FUNNEL_PIPELINE = "Pipeline"
DEAL_FUNNEL_HIGH_CONVICTION = "High Conviction"

def fetch_own_bot_id() -> str:
    response = requests.get(
        "https://slack.com/api/auth.test",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
    )
    data = response.json()
    if not data.get("ok"):
        raise Exception(f"Slack auth.test error: {data.get('error')}")
    bot_id = data.get("bot_id")
    if not bot_id:
        raise Exception("Slack auth.test did not return a bot_id for this token.")
    return bot_id


REPORT_HEADER_MARKER = "Company Intelligence Report"


def fetch_recent_messages() -> str:
    """
    Reconstruct the text of the MOST RECENTLY POSTED report from this
    pipeline's own bot messages in DEST_CHANNEL_ID.
    """
    own_bot_id = fetch_own_bot_id()
    response = requests.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        params={"channel": DEST_CHANNEL_ID, "limit": 200},
    )
    data = response.json()
    if not data.get("ok"):
        raise Exception(f"Slack error: {data.get('error')}")

    all_messages = data.get("messages", [])
    bot_messages = [m for m in all_messages if m.get("bot_id") == own_bot_id]
    print(f"DEBUG: fetched {len(all_messages)} total message(s), "
          f"{len(bot_messages)} bot message(s) total")

    if not bot_messages:
        raise Exception("No messages from this pipeline's bot were found in the channel.")

    chronological = list(reversed(bot_messages))
    header_indices = [
        i for i, m in enumerate(chronological)
        if REPORT_HEADER_MARKER in m.get("text", "")
    ]
    if not header_indices:
        raise Exception(
            f"No message containing {REPORT_HEADER_MARKER!r} found among this "
            "pipeline's bot messages — can't tell where the latest report starts."
        )
    start = header_indices[-1]
    latest_report_messages = chronological[start:]
    print(f"DEBUG: latest report starts at message {start} of {len(chronological)} "
          f"({len(latest_report_messages)} chunk(s))")

    texts = [m.get("text", "") for m in latest_report_messages if m.get("text", "").strip()]

    raw = "".join(texts)
    return html.unescape(raw)


def clean_summary(line: str) -> str:
    """
    Strip the leading "*<url|Company Name>*
    """
    line = re.sub(r'^\*?<https?://[^|>]+\|([^>]+)>\*?:?\s*', '', line)
    line = re.sub(r'^\*([^*]+)\*:?\s*', '', line)
    return line.strip()



COMPANY_LINE_RE = re.compile(
    r'^\*(?:<(https?://[^|>]+)\|([^>]+)>|([^*]+))\*:?\s*(.*)$', re.DOTALL
)


def parse_companies(report: str) -> list[dict]:
    header_marker = "Company Intelligence Report"
    idx = report.find(header_marker)
    if idx != -1:
        newline_idx = report.find("\n", idx)
        report = report[newline_idx + 1:] if newline_idx != -1 else report[idx + len(header_marker):]

    rows = []
    for block in report.split("---"):
        line = block.strip()
        if not line:
            continue
        match = COMPANY_LINE_RE.match(line)
        if not match:
            print(f"DEBUG: skipping unparseable block (not a company line): {line[:80]!r}")
            continue
        url = (match.group(1) or "").strip()
        name = (match.group(2) or match.group(3) or "").strip()
        rows.append({"name": name, "url": url, "summary": clean_summary(line)})
    return rows


REPORT_END_DATE_RE = re.compile(
    r'Company Intelligence Report\s*—\s*[A-Za-z]+ \d{1,2}, \d{4}\s+to\s+([A-Za-z]+ \d{1,2}, \d{4})'
)


def extract_report_date(report: str) -> str:
    """
    Pull the END date of digest.py's source range out of its report header
    """
    match = REPORT_END_DATE_RE.search(report)
    if not match:
        print("Warning: no report date range found in the header; using today's date.")
        return date.today().isoformat()
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        print(f"Warning: could not parse report end date {match.group(1)!r}; using today's date.")
        return date.today().isoformat()


def normalize_name(name: str) -> str:
    """
    Build a matching key for a company name.
    """
    name = re.sub(r'\([^)]*\)', '', name)
    return name.strip().lower()


def _date_only(date_raw: str) -> str:
    """
    Truncate CODA date.
    """
    return date_raw.split("T", 1)[0] if date_raw else ""


def dedupe_within_batch(rows: list[dict]) -> list[dict]:
    """
    Merge entries within the same parsed report that share a company name.
    """
    merged = {}
    order = []
    for r in rows:
        key = normalize_name(r["name"])
        if key in merged:
            try:
                merged[key]["summary"] = merge_summaries(
                    r["name"],
                    merged[key]["summary"],
                    r["summary"],
                )
            except Exception as e:
                print(f"Warning: in-batch merge failed for {r['name']} ({e}); "
                      f"falling back to concatenation.")
                merged[key]["summary"] += "\n\n" + r["summary"]
            if not merged[key]["url"] and r.get("url"):
                merged[key]["url"] = r["url"]
            # This mention is more recent than the one that put this key
            # in `order` — move it to match, as if the old entry were
            # deleted and a new one created here instead.
            order.remove(key)
            order.append(key)
        else:
            merged[key] = {"name": r["name"], "url": r.get("url", ""), "summary": r["summary"]}
            order.append(key)
    return [merged[k] for k in order]

def merge_summaries(company_name: str, old_summary: str, new_summary: str) -> str:
    prompt = f"""You are maintaining a running intelligence summary for the company "{company_name}". Below is the EXISTING summary and a NEW summary from a more recent report. Combine them into a single, cohesive summary written in your own words.

Rules:
- If a fact appears in both, mention it only once.
- Keep all facts that are unique to either summary.
- If there is conflicting information in both, treat it as a progression and point it out.
- Do not add commentary, headers, or labels — output only the combined summary text.
- Do not begin the summary with the company's name (e.g. "{company_name} is..." or "{company_name}:") — the name is stored separately, so start directly with the facts.
- Never drop, compress, or generalize away a fact — every detail from both summaries must survive into the output. Brevity is about wording, never about content.
- Within that constraint, phrase each fact as tersely as possible: cut filler words, hedging, and connective narration ("additionally", "furthermore", "the company also noted", etc.) — join facts with semicolons instead of prose sentences.
EXISTING summary:
{old_summary}

NEW summary:
{new_summary}"""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 600,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    if not response.ok:
        raise Exception(f"Anthropic API error: {response.status_code} — {response.text}")

    data = response.json()
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    combined = "\n".join(text_blocks).strip()
    if not combined:
        raise Exception("Anthropic API returned no text content to merge summaries.")
    return combined


def classify_product_area(company_name: str, summary: str) -> str:
    """
    Ask Claude for a short free-text Product Area label.
    """
    prompt = f"""Based on the summary below, give a short Product Area label (one to three words, e.g. "Fintech", "Dev Tools", "Healthtech") describing what the company "{company_name}" builds or sells.

Respond with ONLY the label on a single line — no punctuation, quotes, headers, or commentary before or after it.

Summary:
{summary}"""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 60,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    if not response.ok:
        raise Exception(f"Anthropic API error: {response.status_code} — {response.text}")

    data = response.json()
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    raw = "\n".join(text_blocks).strip()
    # Guard against a leading preamble line or stray quotes/periods slipping
    # through despite the single-line instruction.
    label = raw.splitlines()[-1].strip(' \t"\'.') if raw else ""
    if not label:
        raise Exception("Anthropic API returned no text content to classify product area.")
    return label


def coda_request(method: str, url: str, headers: dict, max_retries: int = 5, **kwargs):
    response = None
    for attempt in range(max_retries):
        try:
            response = requests.request(method, url, headers=headers, **kwargs)
        except requests.RequestException as e:
            wait = 2 ** attempt
            print(f"Coda request failed ({e}); attempt {attempt + 1}/{max_retries}, waiting {wait}s...")
            time.sleep(wait)
            continue
        if response.status_code != 429 and response.status_code < 500:
            return response
        retry_after = response.headers.get("Retry-After")
        wait = float(retry_after) if retry_after else (2 ** attempt)
        print(f"Coda {response.status_code} (attempt {attempt + 1}/{max_retries}); waiting {wait}s...")
        time.sleep(wait)
    return response

def fetch_existing_rows() -> dict:
    """
    Return a dict of {company_name_lower: {row_id, summary, categorization,
    product_area, assignee, deal_funnel}} for the LATEST row of each company.
    """
    url = f"https://coda.io/apis/v1/docs/{CODA_DOC_ID}/tables/{CODA_TABLE_ID}/rows"
    headers = {"Authorization": f"Bearer {CODA_TOKEN}"}
    existing = {}
    page_token = None
    while True:
        params = {"limit": 500, "valueFormat": "simple", "useColumnNames": "true"}
        if page_token:
            params["pageToken"] = page_token
        response = coda_request("GET", url, headers, params=params)
        if not response.ok:
            raise Exception(f"Coda fetch error: {response.status_code} — {response.text}")
        data = response.json()
        for row in data.get("items", []):
            cells = row.get("values", {})
            name = cells.get("Company", "").strip()
            if not name:
                continue
            key = normalize_name(name)
            row_date = str(cells.get("Date", ""))
            if key in existing and row_date < existing[key]["date"]:
                continue  # a newer row for this company was already seen
            existing[key] = {
                "row_id": row["id"],
                "summary": cells.get("Summary", ""),
                "categorization": cells.get("Category", ""),
                "product_area": cells.get("Product Area", ""),
                "assignee": cells.get("Deal Owner", ""),
                "deal_funnel": cells.get("Deal Funnel", ""),
                "url": cells.get("Website", ""),
                "investors": cells.get("Investors", ""),
                "customers": cells.get("Customers", ""),
                "icp": cells.get("ICP", ""),
                "connectivity": cells.get("Connectivity", ""),
                "push_to_attio": bool(cells.get("Push to Attio", False)),
                "date": row_date,
            }
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return existing

def log_weekly_timeline(rows: list[dict], report_date: str):
    """
    Append one row per company to the timeline table with this week's
    data
    """
    if not rows:
        print("No companies to log to timeline table.")
        return

    url = f"https://coda.io/apis/v1/docs/{CODA_DOC_ID}/tables/{CODA_TIMELINE_TABLE_ID}/rows"
    headers = {
        "Authorization": f"Bearer {CODA_TOKEN}",
        "Content-Type": "application/json",
    }
    new_rows = [
        {
            "cells": [
                {"column": "Company", "value": r["name"]},
                {"column": "Data", "value": r["summary"]},
                {"column": "Date", "value": report_date},
            ]
        }
        for r in rows
    ]
    response = coda_request("POST", url, headers, json={"rows": new_rows})
    if response is None or not response.ok:
        status = getattr(response, "status_code", "no response")
        body = getattr(response, "text", "")
        raise Exception(f"Coda timeline insert error: {status} — {body}")
    print(f"Logged {len(new_rows)} row(s) to timeline table: {[r['name'] for r in rows]}")


def push_to_coda(rows: list[dict], report_date: str):
    """
    Upsert rows into Coda — merge summaries if company already exists.
    """

    if not rows:
        print("No companies to push to Coda.")
        return

    rows = dedupe_within_batch(rows)
    print("Logging this week's data to timeline table...")
    log_weekly_timeline(rows, report_date)

    base_url = f"https://coda.io/apis/v1/docs/{CODA_DOC_ID}/tables/{CODA_TABLE_ID}/rows"
    headers = {
        "Authorization": f"Bearer {CODA_TOKEN}",
        "Content-Type": "application/json",
    }

    print("Scanning existing Coda rows...")
    existing = fetch_existing_rows()
    print(f"Existing keys in Coda: {list(existing.keys())}")

    all_new_rows = []
    rows_to_update = []
    inserted_names = []
    updated_names = []
    committed_names = [] 

    for r in rows:
        key = normalize_name(r["name"])
        print(f"Parsed company: {r['name']!r} -> key: {key!r} -> match: {key in existing}")
        if key in existing:
            old_summary = existing[key]["summary"]
            if old_summary:
                try:
                    merged = merge_summaries(r["name"], old_summary, r["summary"])
                    if merged.strip() == old_summary.strip():
                        print(f"No new information for {r['name']}; summary unchanged.")
                except Exception as e:
                    print(f"Warning: Claude merge failed for {r['name']} ({e}); falling back to concatenation.")
                    merged = old_summary + "\n\n" + r["summary"]
            else:
                merged = r["summary"]

            resolved_deal_funnel = existing[key].get("deal_funnel", "").strip() or DEAL_FUNNEL_SOURCED

            item = {
                "name": r["name"],
                "url": existing[key].get("url", "").strip() or r.get("url", ""),
                "summary": merged,
                "date": report_date,
                "categorization": existing[key].get("categorization", "").strip(),
                "product_area": existing[key].get("product_area", "").strip(),
                "assignee": existing[key].get("assignee", "").strip(),
                "deal_funnel": resolved_deal_funnel,
                "investors": existing[key].get("investors", "").strip(),
                "customers": existing[key].get("customers", "").strip(),
                "icp": existing[key].get("icp", "").strip(),
                "connectivity": existing[key].get("connectivity", "").strip(),
                "push_to_attio": existing[key].get("push_to_attio", False),
            }

            if _date_only(existing[key]["date"]) == report_date:
                rows_to_update.append((existing[key]["row_id"], item))
            else:
                all_new_rows.append(item)
    
            if resolved_deal_funnel == DEAL_FUNNEL_SOURCED:
                updated_names.append(r["name"])
            elif resolved_deal_funnel in (DEAL_FUNNEL_PIPELINE, DEAL_FUNNEL_HIGH_CONVICTION):
                committed_names.append(r["name"])
        else:
            try:
                product_area = classify_product_area(r["name"], r["summary"])
            except Exception as e:
                print(f"Warning: product area classification failed for {r['name']} ({e}); leaving blank.")
                product_area = ""

            all_new_rows.append({
                "name": r["name"],
                "url": r.get("url", ""),
                "summary": r["summary"],
                "date": report_date,
                "categorization": "",
                "product_area": product_area,
                "assignee": "",
                "deal_funnel": DEAL_FUNNEL_SOURCED,
            })
            inserted_names.append(r["name"])

    def build_cells(item: dict) -> list[dict]:
        cells = [
            {"column": "Company", "value": item["name"]},
            {"column": "Summary", "value": item["summary"]},
            {"column": "Date", "value": item["date"]},
            {"column": "Deal Funnel", "value": item["deal_funnel"]},
        ]

        if item["url"]:
            cells.append({"column": "Website", "value": item["url"]})

        if item.get("categorization"):
            cells.append({"column": "Category", "value": item["categorization"]})
        if item.get("product_area"):
            cells.append({"column": "Product Area", "value": item["product_area"]})
        if item.get("assignee"):
            cells.append({"column": "Deal Owner", "value": item["assignee"]})
        if item.get("investors"):
            cells.append({"column": "Investors", "value": item["investors"]})
        if item.get("customers"):
            cells.append({"column": "Customers", "value": item["customers"]})
        if item.get("icp"):
            cells.append({"column": "ICP", "value": item["icp"]})
        if item.get("connectivity"):
            cells.append({"column": "Connectivity", "value": item["connectivity"]})
        if item.get("push_to_attio"):
            cells.append({"column": "Push to Attio", "value": True})
        return cells

    if all_new_rows:
        response = coda_request(
            "POST", base_url, headers,
            json={"rows": [{"cells": build_cells(item)} for item in all_new_rows]},
        )
        if response is None or not response.ok:
            status = getattr(response, "status_code", "no response")
            body = getattr(response, "text", "")
            raise Exception(f"Coda insert error: {status} — {body}")
        print(f"Inserted {len(all_new_rows)} new row(s), in report order: "
              f"{[item['name'] for item in all_new_rows]}")
    else:
        print("Nothing to write.")

    for row_id, item in rows_to_update:
        response = coda_request(
            "PUT", f"{base_url}/{row_id}", headers,
            json={"row": {"cells": build_cells(item)}},
        )
        if response is None or not response.ok:
            status = getattr(response, "status_code", "no response")
            body = getattr(response, "text", "")
            raise Exception(f"Coda update error for {item['name']!r} (row {row_id}): {status} — {body}")
        print(f"Updated existing row for {item['name']!r} in place (already had a row for {report_date}).")

    return {
        "inserted": inserted_names,
        "updated": updated_names,
        "committed": committed_names,
    }

def run():
    print("Fetching recent messages from Slack...")
    report = fetch_recent_messages()
    report_date = extract_report_date(report)
    print("Parsing companies...")
    rows = parse_companies(report)
    for r in rows:
        print(f"RAW parsed: {r['name']!r} -> key: {normalize_name(r['name'])!r}")
    print(f"Found {len(rows)} companies. Pushing to Coda...")
    result = push_to_coda(rows, report_date)
    from Slack_Assignment import notify_report_created
    notify_report_created(result)
    print("Done!")


if __name__ == "__main__":
    run()
