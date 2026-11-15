import re
import requests
import os
from dotenv import load_dotenv

load_dotenv()

from Push_to_CODA import (
    SLACK_TOKEN,
    CODA_TOKEN,
    CODA_DOC_ID,
    CODA_TABLE_ID,
    TEAM_MEMBERS,
    coda_request,
    fetch_existing_rows,
    fetch_own_bot_id,
    normalize_name,
)

ASSIGNEE_OPTIONS = list(TEAM_MEMBERS)

ADDED_HEADER = "Companies added:"
UPDATED_HEADER = "Companies updated:"
COMMITTED_HEADER = "Already in Pipeline/High Conviction (no action needed):"

ASSIGN_RE = re.compile(
    r'(assign|reassign)\s+(\d+)(?:\s*-\s*(\d+))?\s+to\s+([A-Za-z]+)',
    re.IGNORECASE,
)

NOTIFICATION_CHANNEL = os.environ["NOTIFICATION_CHANNEL"]


def post_dm(text: str):
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json"},
        json={"channel": NOTIFICATION_CHANNEL, "text": text, "unfurl_links": False, "unfurl_media": False},
    )
    data = response.json()
    if not data.get("ok"):
        raise Exception(f"Slack post error: {data.get('error')}")


def notify_report_created(result: dict):
    """
    Post a summary of companies added/updated this run, numbered so the recipient can reply 
    "Assign 1-10 to <name>" (added) or "Reassign 1-10 to <name>" (updated).
    """
    if not result:
        return
    added = result.get("inserted", [])
    updated = result.get("updated", [])
    committed = result.get("committed", [])
    if not added and not updated and not committed:
        return

    mention = f"@{ASSIGNEE_OPTIONS[0]} " if ASSIGNEE_OPTIONS else ""
    sections = [f"{mention}Report created."]
    if added:
        sections.append(
            f"*{ADDED_HEADER}*\n" + "\n".join(f"{i + 1}. {name}" for i, name in enumerate(added))
        )
    if updated:
        sections.append(
            f"*{UPDATED_HEADER}*\n" + "\n".join(f"{i + 1}. {name}" for i, name in enumerate(updated))
        )
    if committed:
        sections.append(
            f"*{COMMITTED_HEADER}*\n" + "\n".join(f"- {name}" for name in committed)
        )
    if added or updated:
        example_a = ASSIGNEE_OPTIONS[0] if ASSIGNEE_OPTIONS else "<name>"
        example_b = ASSIGNEE_OPTIONS[-1] if ASSIGNEE_OPTIONS else "<name>"
        sections.append(
            f"Would you like to assign/reassign them? Reply e.g. \"Assign 1-10 to {example_a}\" for "
            f"added companies, or \"Reassign 1-10 to {example_b}\" for updated companies. "
            f"(Valid names: {', '.join(ASSIGNEE_OPTIONS)})"
        )
    post_dm("\n\n".join(sections))


def _parse_numbered_section(text: str, header: str) -> list:
    """Pull the numbered company name from assigned number"""
    names = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.strip("*") == header:
            in_section = True
            continue
        if in_section:
            match = re.match(r'^\d+\.\s+(.+)$', stripped)
            if match:
                names.append(match.group(1).strip())
            elif stripped:
                break
    return names


def _find_latest_report(own_bot_id: str):
    response = requests.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        params={"channel": NOTIFICATION_CHANNEL, "limit": 50},
    )
    data = response.json()
    if not data.get("ok"):
        raise Exception(f"Slack error: {data.get('error')}")
    for m in data.get("messages", []):
        if m.get("bot_id") == own_bot_id and "Report created." in m.get("text", ""):
            return m
    return None


def _assign_rows(names: list, assignee: str, existing: dict):
    """Set the Assignee column for each company name. Returns (updated, not_found)."""

    base_url = f"https://coda.io/apis/v1/docs/{CODA_DOC_ID}/tables/{CODA_TABLE_ID}/rows"
    headers = {"Authorization": f"Bearer {CODA_TOKEN}", "Content-Type": "application/json"}

    updated, not_found = [], []
    for name in names:
        key = normalize_name(name)
        if key not in existing:
            not_found.append(name)
            continue
        response = coda_request(
            "PUT", f"{base_url}/{existing[key]['row_id']}", headers,
            json={"row": {"cells": [{"column": "Deal Owner", "value": assignee}]}},
        )
        if response is None or not response.ok:
            not_found.append(name)
            continue
        updated.append(name)
    return updated, not_found


def check_for_assignment_replies():
    """
    Check if reply is posted
    """
    own_bot_id = fetch_own_bot_id()

    response = requests.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
        params={"channel": NOTIFICATION_CHANNEL, "limit": 1},
    )
    data = response.json()
    if not data.get("ok"):
        raise Exception(f"Slack error: {data.get('error')}")

    messages = data.get("messages", [])
    if not messages:
        print("No messages in the notification channel yet.")
        return
    latest = messages[0]

    if latest.get("bot_id") == own_bot_id:
        print("Latest channel message is our own — nothing new to process.")
        return

    text = latest.get("text", "").strip()
    commands = list(ASSIGN_RE.finditer(text))
    if not commands:
        print(f"Latest DM message isn't an Assign/Reassign command: {text[:80]!r}")
        return

    report = _find_latest_report(own_bot_id)
    if not report:
        post_dm("Couldn't find a recent report to match those numbers against.")
        return

    added_list = _parse_numbered_section(report.get("text", ""), ADDED_HEADER)
    updated_list = _parse_numbered_section(report.get("text", ""), UPDATED_HEADER)
    existing = fetch_existing_rows()

    reply_lines = []
    for match in commands:
        action, start, end, assignee_raw = match.groups()
        start_n = int(start)
        end_n = int(end) if end else start_n

        assignee = next((a for a in ASSIGNEE_OPTIONS if a.lower() == assignee_raw.lower()), None)
        if assignee is None:
            reply_lines.append(
                f"\"{assignee_raw}\" isn't a valid assignee. Valid names: {', '.join(ASSIGNEE_OPTIONS)}."
            )
            continue

        names_list = added_list if action.lower() == "assign" else updated_list

        resolved_names, skipped = [], []
        for n in range(start_n, end_n + 1):
            if 1 <= n <= len(names_list):
                resolved_names.append(names_list[n - 1])
            else:
                skipped.append(n)

        updated, not_found = _assign_rows(resolved_names, assignee, existing)

        if updated:
            reply_lines.append(f"Assigned to {assignee}: " + ", ".join(updated))
        if not_found:
            reply_lines.append("Could not find these companies in Coda: " + ", ".join(not_found))
        if skipped:
            reply_lines.append(
                f"These numbers were out of range (1-{len(names_list)}): " + ", ".join(map(str, skipped))
            )

    if not reply_lines:
        reply_lines.append("Nothing to assign.")
    post_dm("\n".join(reply_lines))


if __name__ == "__main__":
    check_for_assignment_replies()
