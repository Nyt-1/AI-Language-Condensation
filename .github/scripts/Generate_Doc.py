#!/usr/bin/env python3
"""
Fetch every row from a Coda, ask Claude to write a one-report-per-run
deal-flow summary from that data, and render the result to output/report.docx
via pandoc.
"""
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV_VARS = [
    "CODA_API_TOKEN", "CODA_DOC_ID", "CODA_TABLE_ID",
    "SLACK_BOT_TOKEN", "CODA_REPORTS_TABLE_ID",
]

OUTPUT_DIR = "output"

DEST_CHANNEL = os.environ["REPORT_DEST_CHANNEL"]

TZ = ZoneInfo("America/Chicago")

PAGE_BREAK_MARKDOWN = '\n\n```{=openxml}\n<w:p><w:r><w:br w:type="page"/></w:r></w:p>\n```\n\n'

CURRENT_PIPELINE_HEADING = "Current Pipeline"
UNDATED_WEEK_HEADING = "Week of Unspecified Date"

WEEK_WINDOW_DAYS = 7


def _title_block(text: str) -> str:
    """A plain centered, bold paragraph via raw OOXML — deliberately not a
    Word Heading style, to match a flat title look rather than themed
    heading formatting (color/size)."""
    return (
        '\n\n```{=openxml}\n'
        f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r></w:p>\n'
        '```\n\n'
    )


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def require_env_vars() -> dict:
    values = {name: os.environ.get(name, "") for name in REQUIRED_ENV_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        fail(f"missing required environment variable(s): {', '.join(missing)}")
    return values


def fetch_coda_rows(doc_id: str, table_id: str, token: str) -> list:
    """
    GET every row of a Coda table, following nextPageLink until exhausted. 
    """
    url = f"https://coda.io/apis/v1/docs/{doc_id}/tables/{table_id}/rows"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"useColumnNames": "true", "limit": 500, "sortBy": "natural"}

    rows = []
    next_url = url
    next_params = params
    while next_url:
        try:
            response = requests.get(next_url, headers=headers, params=next_params, timeout=30)
        except requests.RequestException as e:
            fail(f"Coda request failed: {e}")
        if not response.ok:
            fail(f"Coda API error {response.status_code}: {response.text}")
        data = response.json()
        rows.extend(item.get("values", {}) for item in data.get("items", []))
        next_url = data.get("nextPageLink")
        next_params = None

    return rows


def _normalize_company_name(name: str) -> str:
    name = re.sub(r'\([^)]*\)', '', name or "")
    return re.sub(r'[^a-z0-9]', '', name.lower())


def dedupe_latest_per_company(rows: list) -> list:
    """Keep only the latest mention of each company, newest-first."""
    latest = {}
    order = []
    for i, row in enumerate(reversed(rows)):
        name = (row.get("Company") or "").strip()
        key = _normalize_company_name(name) if name else f"__unnamed_{i}__"
        if key not in latest:
            order.append(key)
            latest[key] = row
        # else: this is an older mention of a company already kept — skip it.
    return [latest[key] for key in order]


def _week_key(date_raw: str) -> str:
    """
    Truncate to just the calendar date.
    """
    return date_raw.split("T", 1)[0] if date_raw else ""


def _week_range_heading(end_date_str: str) -> str:
    stored_end = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    start = stored_end - timedelta(days=WEEK_WINDOW_DAYS)
    end = stored_end - timedelta(days=1)
    return f"{start.strftime('%m.%d.%Y')}-{end.strftime('%m.%d.%Y')}"


def document_date_range(rows: list) -> str | None:
    dates = sorted({_week_key(str(row.get("Date", "")).strip()) for row in rows} - {""})
    if not dates:
        return None
    earliest = datetime.strptime(dates[0], "%Y-%m-%d").date() - timedelta(days=WEEK_WINDOW_DAYS)
    latest = datetime.strptime(dates[-1], "%Y-%m-%d").date() - timedelta(days=1)
    return f"{earliest.strftime('%m.%d.%Y')}-{latest.strftime('%m.%d.%Y')}"


def group_rows_by_week(rows: list) -> list:
    buckets = {}
    order = []
    for row in rows:
        date = _week_key(str(row.get("Date", "")).strip())
        if date not in buckets:
            order.append(date)
            buckets[date] = []
        buckets[date].append(row)

    dated = sorted((d for d in order if d), key=lambda d: d, reverse=True)
    groups = [{"key": d, "heading": _week_range_heading(d), "rows": buckets[d]} for d in dated]
    if "" in buckets:
        groups.append({"key": None, "heading": UNDATED_WEEK_HEADING, "rows": buckets[""]})
    return groups


FUNNEL_SECTION_ORDER = ["High Conviction", "Pipeline", "Sourcing"]


def _format_rows_as_markdown(rows: list) -> str:
    sections = {}
    order = []
    for row in rows:
        funnel = (row.get("Deal Funnel") or "").strip() or "Unspecified"
        if funnel not in sections:
            order.append(funnel)
            sections[funnel] = []
        sections[funnel].append(row)

    order.sort(key=lambda f: (
        FUNNEL_SECTION_ORDER.index(f) if f in FUNNEL_SECTION_ORDER else len(FUNNEL_SECTION_ORDER)
    ))

    parts = []
    for funnel in order:
        parts.append(f"## {funnel}")
        for row in sections[funnel]:
            name = " ".join((row.get("Company") or "").split())
            summary = " ".join((row.get("Summary") or "").split())
            url = (row.get("Website") or "").strip()
            product_area = " ".join((row.get("Product Area") or "").split())
            label = f"[**{name}**]({url})" if url else f"[**{name}**]{{.underline}}"
            if product_area:
                label += f" *({product_area})*"
            parts.append(f"- {label}: {summary}")
        parts.append("")
    return "\n".join(parts).strip()


def generate_week_section_markdown(rows: list) -> str:
    """Format ONE week's rows into bullets, keeping only the latest entry
    per company within that week."""
    return _format_rows_as_markdown(dedupe_latest_per_company(rows))


def generate_weekly_sections(week_groups: list) -> list:
    """Generate each week's section independently, each labeled with its
    own heading."""
    sections = []
    for i, group in enumerate(week_groups, 1):
        print(f"  Generating week {i}/{len(week_groups)}: {group['heading']}...")
        body = generate_week_section_markdown(group["rows"])
        sections.append(f"{_title_block(group['heading'])}{body}")
    return sections


def assemble_full_report(general_md: str, week_sections: list) -> str:
    parts = [f"{_title_block(CURRENT_PIPELINE_HEADING)}{general_md.strip()}"]
    parts.extend(s.strip() for s in week_sections)
    return PAGE_BREAK_MARKDOWN.join(parts)


def generate_report_markdown(rows: list) -> str:
    return _format_rows_as_markdown(rows)


def build_full_report(all_rows: list) -> tuple[str, str]:
    deduped_rows = dedupe_latest_per_company(all_rows)
    print(f"{len(deduped_rows)} unique company row(s) after keeping only the latest per company.")

    print("Generating current-pipeline section...")
    general_md = generate_report_markdown(deduped_rows)

    week_groups = group_rows_by_week(all_rows)
    print(f"Grouped all {len(all_rows)} row(s) into {len(week_groups)} week(s) of history.")
    print("Generating weekly-history sections...")
    week_sections = generate_weekly_sections(week_groups)

    full_markdown = assemble_full_report(general_md, week_sections)
    date_range = document_date_range(all_rows)
    filename_stem = f"Report_{date_range}" if date_range else "Report"
    return full_markdown, filename_stem


def build_range_report(all_rows: list, from_date_str: str, to_date_str: str) -> tuple[str, str]:
    """
    The same per-week sections build_full_report generates for its weekly breakdown
    """
    try:
        from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
        to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
    except ValueError as e:
        fail(f"FROM_DATE/TO_DATE must be YYYY-MM-DD: {e}")

    week_groups = group_rows_by_week(all_rows)
    in_range = [
        g for g in week_groups
        if g["key"] and from_date < datetime.strptime(g["key"], "%Y-%m-%d").date() <= to_date
    ]
    if not in_range:
        fail(f"no weeks found with a bucket date between {from_date_str} (exclusive) and {to_date_str} (inclusive).")

    print(f"{len(in_range)} week(s) fall between {from_date_str} and {to_date_str}: "
          f"{', '.join(g['heading'] for g in reversed(in_range))}")
    week_sections = generate_weekly_sections(in_range)
    full_markdown = PAGE_BREAK_MARKDOWN.join(s.strip() for s in week_sections)

    earliest_start = datetime.strptime(in_range[-1]["key"], "%Y-%m-%d").date() - timedelta(days=WEEK_WINDOW_DAYS)
    latest_end = datetime.strptime(in_range[0]["key"], "%Y-%m-%d").date() - timedelta(days=1)
    filename_stem = f"Report_{earliest_start.strftime('%m.%d.%Y')}-{latest_end.strftime('%m.%d.%Y')}"
    return full_markdown, filename_stem


def render_docx(markdown_text: str, markdown_path: str, docx_path: str) -> None:
    os.makedirs(os.path.dirname(docx_path) or ".", exist_ok=True)
    with open(markdown_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    try:
        result = subprocess.run(
            ["pandoc", markdown_path, "-o", docx_path],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        fail("pandoc is not installed / not on PATH.")
    if result.returncode != 0:
        fail(f"pandoc failed to convert markdown to docx: {result.stderr.strip()}")


def upload_file_to_slack(channel_id: str, file_path: str, token: str, initial_comment: str = "") -> str:
    """
    Upload a file to Slack.
    """
    filename = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    response = requests.post(
        "https://slack.com/api/files.getUploadURLExternal",
        headers={"Authorization": f"Bearer {token}"},
        data={"filename": filename, "length": file_size},
        timeout=30,
    )
    data = response.json()
    if not data.get("ok"):
        fail(f"Slack files.getUploadURLExternal error: {data.get('error')}")
    upload_url, file_id = data["upload_url"], data["file_id"]

    with open(file_path, "rb") as f:
        upload_response = requests.post(upload_url, files={"file": f}, timeout=60)
    if not upload_response.ok:
        fail(f"Slack file upload failed: {upload_response.status_code} {upload_response.text}")

    complete_response = requests.post(
        "https://slack.com/api/files.completeUploadExternal",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "files": [{"id": file_id, "title": filename}],
            "channel_id": channel_id,
            "initial_comment": initial_comment,
        },
        timeout=30,
    )
    complete_data = complete_response.json()
    if not complete_data.get("ok"):
        fail(f"Slack files.completeUploadExternal error: {complete_data.get('error')}")

    files = complete_data.get("files") or []
    if not files or not files[0].get("permalink"):
        fail(f"Slack files.completeUploadExternal response had no file permalink: {complete_data}")
    return files[0]["permalink"]


def push_report_link_to_coda(
    doc_id: str, table_id: str, token: str,
    report_link: str, range_requested: str, person_requested: str,
) -> None:
    url = f"https://coda.io/apis/v1/docs/{doc_id}/tables/{table_id}/rows"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "rows": [
            {
                "cells": [
                    {"column": "Report", "value": report_link},
                    {"column": "Date Requested", "value": datetime.now(TZ).isoformat()},
                    {"column": "Range Requested", "value": range_requested},
                    {"column": "Person Requested", "value": person_requested},
                ]
            }
        ]
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if not response.ok:
        fail(f"Coda reports-table insert error {response.status_code}: {response.text}")


def main() -> None:
    env = require_env_vars()

    full_report = os.environ.get("FULL_REPORT", "true").strip().lower() != "false"
    from_date_str = os.environ.get("FROM_DATE", "").strip()
    to_date_str = os.environ.get("TO_DATE", "").strip()
    person_requested = os.environ.get("PERSON_REQUESTED", "").strip()
    range_requested = "Full Report" if full_report else f"{from_date_str} to {to_date_str}"

    print(f"Fetching rows from Coda table {env['CODA_TABLE_ID']}...")
    all_rows = fetch_coda_rows(env["CODA_DOC_ID"], env["CODA_TABLE_ID"], env["CODA_API_TOKEN"])
    print(f"Fetched {len(all_rows)} row(s).")
    if not all_rows:
        fail("no rows returned from Coda — nothing to report on.")

    if full_report:
        full_markdown, filename_stem = build_full_report(all_rows)
    else:
        if not from_date_str or not to_date_str:
            fail("FULL_REPORT is false but FROM_DATE/TO_DATE were not provided.")
        full_markdown, filename_stem = build_range_report(all_rows, from_date_str, to_date_str)

    markdown_path = os.path.join(OUTPUT_DIR, f"{filename_stem}.md")
    docx_path = os.path.join(OUTPUT_DIR, f"{filename_stem}.docx")

    print(f"Rendering {docx_path} with pandoc...")
    render_docx(full_markdown, markdown_path, docx_path)

    print(f"Uploading {docx_path} to Slack channel {DEST_CHANNEL}...")
    report_link = upload_file_to_slack(DEST_CHANNEL, docx_path, env["SLACK_BOT_TOKEN"])

    print(f"Logging report link to Coda table {env['CODA_REPORTS_TABLE_ID']}...")
    push_report_link_to_coda(
        env["CODA_DOC_ID"], env["CODA_REPORTS_TABLE_ID"], env["CODA_API_TOKEN"],
        report_link, range_requested, person_requested,
    )

    print(f"Done! Wrote {docx_path}, posted it to Slack, and logged the link to Coda.")


if __name__ == "__main__":
    main()
