import argparse
import base64
import os
import re
import requests
import anthropic
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

from Push_to_CODA import (
    CODA_TOKEN,
    CODA_DOC_ID,
    CODA_TABLE_ID,
    coda_request,
    normalize_name,
    merge_summaries,
    classify_product_area,
    log_weekly_timeline,
    _date_only,
    TEAM_MEMBERS,
)

SLACK_TOKEN = os.environ["SLACK_BOT_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
SKILL_ID = os.environ["PIPELINE_SKILL_ID"]

PHOTO_LINK_SKILL_ID = os.environ["PHOTO_LINK_SKILL_ID"]


SOURCE_CHANNEL = os.environ["PIPELINE_SOURCE_CHANNEL"]
DEST_CHANNEL_ID = os.environ["DEST_CHANNEL_ID"]

DEFAULT_WINDOW_DAYS = 7
TZ = ZoneInfo("America/Chicago")

MAX_IMAGES = 20
MAX_IMAGE_BYTES = 5 * 1024 * 1024
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
DEAL_FUNNEL_VALUE = "Pipeline"

DEAL_FUNNEL_HIGH_CONVICTION = "High Conviction"
CARRY_FORWARD_DEAL_FUNNELS = {DEAL_FUNNEL_VALUE, DEAL_FUNNEL_HIGH_CONVICTION}

OWNERS = {name.lower(): name for name in TEAM_MEMBERS}

def normalize_owner(owner_raw: str) -> str:
    owner_raw = owner_raw.strip()
    if not owner_raw or owner_raw.lower() not in OWNERS:
        return ""
    return OWNERS.get(owner_raw.lower(), owner_raw)


client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def resolve_range(from_str=None, to_str=None):
    now = datetime.now(TZ)

    if from_str is None and to_str is None:
        to_dt = now
        from_dt = datetime.combine(
            (now - timedelta(days=DEFAULT_WINDOW_DAYS)).date(), datetime.min.time(), TZ
        )
    else:
        from_date = datetime.strptime(from_str, "%m-%d-%Y").date() if from_str else None
        to_date = datetime.strptime(to_str, "%m-%d-%Y").date() if to_str else None

        to_dt = (
            datetime.combine(to_date, datetime.min.time(), TZ) if to_date else now
        )
        if from_date:
            from_dt = datetime.combine(from_date, datetime.min.time(), TZ)
        else:
            from_dt = datetime.combine(
                (to_dt - timedelta(days=DEFAULT_WINDOW_DAYS)).date(), datetime.min.time(), TZ
            )

    if from_dt >= to_dt:
        raise ValueError(
            f"--from ({from_dt.date()}) must be before --to ({to_dt.date()})."
        )

    return from_dt.timestamp(), to_dt.timestamp()


_user_name_cache = {}


def resolve_user_name(user_id):
    """Resolve a Slack user ID to a display name, so the transcript can tell
    the skill who sent each message."""
    if not user_id:
        return "Unknown"
    if user_id in _user_name_cache:
        return _user_name_cache[user_id]
    response = requests.get(
        "https://slack.com/api/users.info",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        params={"user": user_id},
    )
    data = response.json()
    if not data.get("ok"):
        print(f"Warning: users.info failed for {user_id} ({data.get('error')}); "
              f"reporting sender as 'Unknown'. Check the bot token has the "
              f"users:read scope.")
    profile = data.get("user", {}) if data.get("ok") else {}
    name = profile.get("real_name") or profile.get("name") or "Unknown"
    _user_name_cache[user_id] = name
    return name


def _download_slack_image(url):
    if not url:
        return None
    response = requests.get(url, headers={"Authorization": f"Bearer {SLACK_TOKEN}"})
    if not response.ok or len(response.content) > MAX_IMAGE_BYTES:
        return None
    return base64.standard_b64encode(response.content).decode("utf-8")


def _fetch_thread_replies(channel_id, thread_ts):
    """Returns the reply messages only (excludes the parent,
    which the caller already has from conversations.history)."""
    replies = []
    cursor = None
    while True:
        params = {"channel": channel_id, "ts": thread_ts, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        response = requests.get(
            "https://slack.com/api/conversations.replies",
            headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
            params=params,
        )
        data = response.json()
        if not data.get("ok"):
            print(f"Warning: conversations.replies failed for thread {thread_ts} "
                  f"in {channel_id} ({data.get('error')}); skipping its replies.")
            return replies
        replies.extend(data.get("messages", [])[1:])
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return replies


def fetch_messages(channel_id, oldest_ts, latest_ts):
    """Fetch the deal-pipeline channel's messages into one chronological
    transcript, each line tagged with its sender's name. Includes thread
    replies.
    """

    top_level = []
    cursor = None
    while True:
        params = {
            "channel": channel_id,
            "oldest": oldest_ts,
            "latest": latest_ts,
            "inclusive": "true",
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        response = requests.get(
            "https://slack.com/api/conversations.history",
            headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
            params=params,
        )
        data = response.json()
        if not data.get("ok"):
            raise Exception(f"Slack error ({channel_id}): {data.get('error')}")
        for m in data.get("messages", []):
            top_level.append(m)
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    top_level.sort(key=lambda m: float(m.get("ts", 0)))

    all_entries = []
    for m in top_level:
        all_entries.append(m)
        if m.get("reply_count", 0) > 0 and m.get("thread_ts") == m.get("ts"):
            replies = [
                r for r in _fetch_thread_replies(channel_id, m["ts"])
                if oldest_ts <= float(r.get("ts", 0)) <= latest_ts
            ]
            replies.sort(key=lambda r: float(r.get("ts", 0)))
            all_entries.extend(replies)

    text_lines = []
    image_blocks = []
    image_count = 0
    for m in all_entries:
        ts = float(m.get("ts", 0))
        readable_time = datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M")
        sender = resolve_user_name(m.get("user"))
        if m.get("text"):
            text_lines.append(f"[{readable_time}] ({sender}): {m['text']}")
        for f in m.get("files", []) or []:
            mimetype = f.get("mimetype", "")
            fname = f.get("name") or f.get("title") or "unnamed file"
            image_data = None
            if mimetype in SUPPORTED_IMAGE_TYPES and image_count < MAX_IMAGES:
                image_data = _download_slack_image(f.get("url_private"))
            if image_data is not None:
                image_blocks.append({"type": "text", "text": f"[{readable_time}] ({sender}) shared an image:"})
                image_blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mimetype, "data": image_data},
                })
                image_count += 1
            else:
                # Anything we can't actually read (pptx/docx/pdf, an image
                text_lines.append(
                    f"[{readable_time}] ({sender}) shared an attachment: {fname}, view in Slack."
                )

    content_blocks = []
    if text_lines:
        content_blocks.append({"type": "text", "text": "\n".join(text_lines)})
    content_blocks.extend(image_blocks)
    return content_blocks, text_lines


SKILLS = [
    {"type": "custom", "skill_id": SKILL_ID, "version": "latest"},
    {"type": "custom", "skill_id": PHOTO_LINK_SKILL_ID, "version": "latest"},
]

TOOLS = [
    {"type": "code_execution_20250825", "name": "code_execution"},
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 20},
    {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 20},
]


def generate_pipeline_report(content_blocks, from_dt, to_dt):
    range_desc = f"{from_dt.strftime('%Y-%m-%d')} to {to_dt.strftime('%Y-%m-%d')}"
    intro_text = (
        f"""Generate a deal pipeline digest from the following Slack messages ({range_desc}). Each message is prefixed with its timestamp and the sender's name — always log the sender's name, it's needed to determine deal ownership. Always use the deal-pipeline-digest skill to generate this report. Use ONLY the information contained in these messages for facts — do not add facts from outside knowledge or general web search results.\n---\n"""
    )
    messages = [
        {
            "role": "user",
            "content": (
                [{"type": "text", "text": intro_text}]
                + content_blocks
                + [{"type": "text", "text": "\n---"}]
            ),
        }
    ]
    response = client.beta.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        betas=["code-execution-2025-08-25", "skills-2025-10-02"],
        container={"skills": SKILLS},
        tools=TOOLS,
        messages=messages,
    )

    for _ in range(20):
        if response.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": response.content})
        response = client.beta.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            betas=["code-execution-2025-08-25", "skills-2025-10-02"],
            container={
                "id": response.container.id,
                "skills": SKILLS,
            },
            tools=TOOLS,
            messages=messages,
        )
    else:
        print("Warning: hit max continuations (20) while stop_reason is still "
              "'pause_turn'; report may be incomplete.")

    raw_text = "\n".join(
        block.text for block in response.content if block.type == "text"
    )
    return _strip_preamble(raw_text)


_ENTRY_START_RE = re.compile(r'^\*(<https?://\S+\|[^>]+>|[^*]+)\*:')


def _strip_preamble(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "---" or _ENTRY_START_RE.match(stripped):
            return "\n".join(lines[i:]).strip()
    return text.strip()


ENTRY_RE = re.compile(
    r'^\*(?:<(https?://[^|>]+)\|([^>]+)>|([^*]+))\*:\s*(.*?)\s*\[Owner:\s*([^\]]+)\]\s*$',
    re.DOTALL,
)


def parse_pipeline_entries(report: str) -> list[dict]:
    rows = []
    for block in report.split("---"):
        line = block.strip()
        if not line:
            continue
        match = ENTRY_RE.match(line)
        if not match:
            print(f"DEBUG: skipping unparseable block (not a valid entry): {line[:80]!r}")
            continue
        url, name_linked, name_bare, summary, owner_raw = match.groups()
        name = (name_linked or name_bare or "").strip()
        owner = normalize_owner(owner_raw)
        rows.append({
            "name": name,
            "url": (url or "").strip(),
            "summary": summary.strip(),
            "owner": owner,
        })
    return rows

def dedupe_within_batch_with_owner(rows: list[dict]) -> list[dict]:
    """Dedupes within a batch while considering owner."""
    merged = {}
    order = []
    for r in rows:
        key = normalize_name(r["name"])
        if key in merged:
            try:
                merged[key]["summary"] = merge_summaries(
                    r["name"], merged[key]["summary"], r["summary"],
                )
            except Exception as e:
                print(f"Warning: in-batch merge failed for {r['name']} ({e}); "
                      f"falling back to concatenation.")
                merged[key]["summary"] += "\n\n" + r["summary"]
            if not merged[key]["owner"] and r.get("owner"):
                merged[key]["owner"] = r["owner"]
            if not merged[key]["url"] and r.get("url"):
                merged[key]["url"] = r["url"]
            # This mention is more recent than the one that put this key
            # in `order` — move it to match, as if the old entry were
            # deleted and a new one created here instead.
            order.remove(key)
            order.append(key)
        else:
            merged[key] = {
                "name": r["name"], "url": r.get("url", ""),
                "summary": r["summary"], "owner": r.get("owner", ""),
            }
            order.append(key)
    return [merged[k] for k in order]


def fetch_existing_rows_with_funnel() -> dict:
    """Same "latest row per company wins" rule as log.py's
    fetch_existing_rows, but also reads the Deal Funnel cell."""
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
                continue
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


def push_to_pipeline_coda(rows: list[dict], report_date: str):
    """Upsert rows. A duplicate's Category/Assignee/Deal Funnel only carry forward if its existing Deal Funnel is already "committed", otherwise all three reset as if this were a brand new pipeline entry. Product Area always carries forward regardless of funnel stage. The summary always merges either
    way."""
    if not rows:
        print("No informational entries to push to Coda.")
        return

    rows = dedupe_within_batch_with_owner(rows)
    print("Logging this week's data to timeline table...")
    log_weekly_timeline(rows, report_date)

    base_url = f"https://coda.io/apis/v1/docs/{CODA_DOC_ID}/tables/{CODA_TABLE_ID}/rows"
    headers = {"Authorization": f"Bearer {CODA_TOKEN}", "Content-Type": "application/json"}

    print("Scanning existing Coda rows...")
    existing = fetch_existing_rows_with_funnel()

    all_new_rows = []
    rows_to_update = []  # (row_id, item) pairs — same company, same date already in Coda
    inserted_names = []
    updated_names = []

    for r in rows:
        key = normalize_name(r["name"])
        if key in existing:
            old_summary = existing[key]["summary"]
            if old_summary:
                try:
                    merged = merge_summaries(r["name"], old_summary, r["summary"])
                except Exception as e:
                    print(f"Warning: Claude merge failed for {r['name']} ({e}); falling back to concatenation.")
                    merged = old_summary + "\n\n" + r["summary"]
            else:
                merged = r["summary"]

            old_deal_funnel = existing[key].get("deal_funnel", "").strip()
            product_area = existing[key].get("product_area", "").strip()
            investors = existing[key].get("investors", "").strip()
            customers = existing[key].get("customers", "").strip()
            icp = existing[key].get("icp", "").strip()
            connectivity = existing[key].get("connectivity", "").strip()
            push_to_attio = existing[key].get("push_to_attio", False)
            if old_deal_funnel in CARRY_FORWARD_DEAL_FUNNELS:
                categorization = existing[key].get("categorization", "").strip()
                assignee = existing[key].get("assignee", "").strip() or r.get("owner", "")
                deal_funnel = old_deal_funnel
            else:
                categorization = ""
                assignee = r.get("owner", "")
                deal_funnel = DEAL_FUNNEL_VALUE

            item = {
                "name": r["name"],
                "url": existing[key].get("url", "").strip() or r.get("url", ""),
                "summary": merged,
                "date": report_date,
                "categorization": categorization,
                "product_area": product_area,
                "assignee": assignee,
                "deal_funnel": deal_funnel,
                "investors": investors,
                "customers": customers,
                "icp": icp,
                "connectivity": connectivity,
                "push_to_attio": push_to_attio,
            }

            if _date_only(existing[key]["date"]) == report_date:
                rows_to_update.append((existing[key]["row_id"], item))
            else:
                all_new_rows.append(item)
            updated_names.append(r["name"])
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
                "assignee": r.get("owner", ""),
                "deal_funnel": DEAL_FUNNEL_VALUE,
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
    }


def post_to_slack(channel_id, text):
    limit = 39000
    chunks = [text[i:i + limit] for i in range(0, len(text), limit)]
    for chunk in chunks:
        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {SLACK_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"channel": channel_id, "text": chunk, "unfurl_links": False, "unfurl_media": False},
        )
        data = response.json()
        if not data.get("ok"):
            raise Exception(f"Slack post error: {data.get('error')}")


def post_pipeline_report(result: dict):
    """Post a plain summary of what push_to_pipeline_coda"""
    if not result:
        return
    added = result.get("inserted", [])
    updated = result.get("updated", [])
    if not added and not updated:
        print("No companies added or updated in Coda this run.")
        return

    sections = ["*Coda sync complete.*"]
    if added:
        sections.append(
            "*Added:*\n" + "\n".join(f"{i + 1}. {name}" for i, name in enumerate(added))
        )
    if updated:
        sections.append(
            "*Updated:*\n" + "\n".join(f"{i + 1}. {name}" for i, name in enumerate(updated))
        )
    post_to_slack(DEST_CHANNEL_ID, "\n\n".join(sections))


def run(from_str=None, to_str=None):
    oldest_ts, latest_ts = resolve_range(from_str, to_str)
    from_dt, to_dt = datetime.fromtimestamp(oldest_ts, TZ), datetime.fromtimestamp(latest_ts, TZ)
    print(f"Fetching messages from {from_dt.strftime('%Y-%m-%d')} to {to_dt.strftime('%Y-%m-%d')}...")

    content_blocks, text_lines = fetch_messages(SOURCE_CHANNEL, oldest_ts, latest_ts)
    if not content_blocks:
        print("No messages in this range, skipping.")
        return

    print("Generating deal pipeline digest with Claude...")
    report = generate_pipeline_report(content_blocks, from_dt, to_dt)


    rows = parse_pipeline_entries(report)
    print(f"Found {len(rows)} informational entr{'y' if len(rows) == 1 else 'ies'}.")

    print("Posting to Slack...")
    header = (
        f"*📊 Deal Pipeline Digest — "
        f"{from_dt.strftime('%B %d, %Y')} to {to_dt.strftime('%B %d, %Y')}*\n\n"
    )
    post_to_slack(DEST_CHANNEL_ID, header + report)

    print("Pushing to Coda...")
    report_date = to_dt.date().isoformat()
    result = push_to_pipeline_coda(rows, report_date)
    post_pipeline_report(result)

    print("Done!")


def _parse_args():
    parser = argparse.ArgumentParser(description="Generate and push a deal pipeline digest from Slack to Coda.")
    parser.add_argument("--from", dest="from_date", help="Start date, MM-DD-YYYY, inclusive. Defaults to 7 days before --to (or before now).")
    parser.add_argument("--to", dest="to_date", help="End date, MM-DD-YYYY, inclusive. Defaults to now.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(args.from_date, args.to_date)
