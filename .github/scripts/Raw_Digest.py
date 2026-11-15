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

SLACK_TOKEN = os.environ["SLACK_BOT_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
SOURCE_CHANNEL = os.environ["RAW_SOURCE_CHANNEL"]
DEST_CHANNEL_ID = os.environ["DEST_CHANNEL_ID"]
SKILL_ID = os.environ["COMPANY_SKILL_ID"]
PHOTO_LINK_SKILL_ID = os.environ["PHOTO_LINK_SKILL_ID"]

DEFAULT_WINDOW_DAYS = 7
TZ = ZoneInfo("America/Chicago")

MAX_IMAGES = 20
MAX_IMAGE_BYTES = 5 * 1024 * 1024
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def resolve_range(from_str=None, to_str=None):
    """
    Turn optional --from/--to CLI dates (MM-DD-YYYY) into a concrete
    (oldest_ts, latest_ts) window, in the America/Chicago (CT) timezone.
    Both --from and --to land on midnight CT (the very start) of their
    date, so --to is exclusive of that date:
      - neither given: midnight CT, DEFAULT_WINDOW_DAYS days ago, through now.
      - both given: midnight CT on --from through midnight CT on --to.
      - only --from: from midnight CT on that date through now.
      - only --to: midnight CT DEFAULT_WINDOW_DAYS days before --to, through
        midnight CT on --to.
    """
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


def _fetch_thread_replies(channel_id, thread_ts):
    """conversations.history returns thread PARENTS but never their
    replies — those need a separate conversations.replies call per thread.
    Without this, any company mention posted as a reply is invisible to the
    digest entirely. Returns the reply messages only (excludes the parent,
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
                  f"({data.get('error')}); skipping its replies.")
            return replies
        # First message in a conversations.replies response is always the
        # parent itself (already fetched via conversations.history) — skip it.
        replies.extend(data.get("messages", [])[1:])
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return replies


def fetch_messages(channel_id, oldest_ts, latest_ts):
    all_messages = []
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
            raise Exception(f"Slack error: {data.get('error')}")
        all_messages.extend(data.get("messages", []))
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    # Sort oldest -> newest BEFORE splicing in replies — the sort has to
    # happen first so each thread's replies can be inserted right after
    # their parent, rather than getting scattered by a later flat sort
    # against unrelated messages' timestamps.
    all_messages.sort(key=lambda m: float(m.get("ts", 0)))

    ordered = []
    for m in all_messages:
        ordered.append(m)
        # A thread parent has thread_ts == its own ts and reply_count > 0.
        # Replies are appended immediately after their parent — an
        # extension of it — rather than merged back into one global
        # chronological sort, so a message posted elsewhere in the channel
        # between the parent and a slow reply can't split the thread apart
        # in the transcript.
        if m.get("reply_count", 0) > 0 and m.get("thread_ts") == m.get("ts"):
            replies = [
                r for r in _fetch_thread_replies(channel_id, m["ts"])
                if oldest_ts <= float(r.get("ts", 0)) <= latest_ts
            ]
            replies.sort(key=lambda r: float(r.get("ts", 0)))
            ordered.extend(replies)
    all_messages = ordered

    # Keep the transcript as ONE contiguous text block — the report skill's
    # step 1 has Claude copy this verbatim to a file for mechanical candidate
    # extraction, and splitting it into one block per message forces Claude to
    # reconstruct the transcript from many small blocks first, which is exactly
    # the kind of manual retyping that step exists to avoid (and drops
    # companies). Images are appended as separate blocks after the transcript.
    text_lines = []
    image_blocks = []
    image_count = 0
    for m in all_messages:
        ts = float(m.get("ts", 0))
        readable_time = datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M")
        if m.get("text"):
            text_lines.append(f"[{readable_time}] {m['text']}")
        for f in m.get("files", []) or []:
            mimetype = f.get("mimetype", "")
            fname = f.get("name") or f.get("title") or "unnamed file"
            image_data = None
            if mimetype in SUPPORTED_IMAGE_TYPES and image_count < MAX_IMAGES:
                image_data = _download_slack_image(f.get("url_private"))
            if image_data is not None:
                image_blocks.append({"type": "text", "text": f"[{readable_time}] (image shared in the message above)"})
                image_blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mimetype, "data": image_data},
                })
                image_count += 1
            else:
                # Anything we can't actually read (pptx/docx/pdf, an image
                # over MAX_IMAGES, or a failed download) still gets noted in
                # the transcript so it isn't silently invisible — just
                # without its content.
                text_lines.append(
                    f"[{readable_time}] (attachment shared in the message above: {fname}, "
                    f"{mimetype or 'unknown type'} — not read; view in Slack)"
                )

    content_blocks = []
    if text_lines:
        content_blocks.append({"type": "text", "text": "\n".join(text_lines)})
    content_blocks.extend(image_blocks)
    return content_blocks


def _download_slack_image(url):
    if not url:
        return None
    response = requests.get(url, headers={"Authorization": f"Bearer {SLACK_TOKEN}"})
    if not response.ok or len(response.content) > MAX_IMAGE_BYTES:
        return None
    return base64.standard_b64encode(response.content).decode("utf-8")


SKILLS = [
    {"type": "custom", "skill_id": SKILL_ID, "version": "latest"},
    {"type": "custom", "skill_id": PHOTO_LINK_SKILL_ID, "version": "latest"},
]

TOOLS = [
    {"type": "code_execution_20250825", "name": "code_execution"},
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 20},
    {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 20},
]


def generate_company_report(content_blocks, from_dt, to_dt):
    range_desc = f"{from_dt.strftime('%Y-%m-%d')} to {to_dt.strftime('%Y-%m-%d')}"
    intro_text = (
        f"Generate a company intelligence report from the following "
        f"Slack messages ({range_desc}). Use ONLY the information "
        "contained in these messages for facts — do not add facts "
        "from outside knowledge or general web search results. If a message "
        "includes a shared image or a link, use the photo-link-reader skill "
        "to read facts from it. You may also use web search "
        "only to find each company's official website URL for the "
        "linked name.\n---\n"
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

    # Skills can pause long turns; let Claude continue until done.
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


def post_to_slack(channel_id, text):
    limit = 39000
    chunks = [text[i:i+limit] for i in range(0, len(text), limit)]
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


def run_digest(from_str=None, to_str=None):
    oldest_ts, latest_ts = resolve_range(from_str, to_str)
    from_dt, to_dt = datetime.fromtimestamp(oldest_ts, TZ), datetime.fromtimestamp(latest_ts, TZ)
    print(f"Fetching messages from {from_dt.strftime('%Y-%m-%d')} to {to_dt.strftime('%Y-%m-%d')}...")

    content_blocks = fetch_messages(SOURCE_CHANNEL, oldest_ts, latest_ts)
    if not content_blocks:
        print("No messages in this range, skipping.")
        return
    print("Generating company report with Claude...")
    report = generate_company_report(content_blocks, from_dt, to_dt)
    print("Posting to Slack...")
    header = (
        f"*📊 Company Intelligence Report — "
        f"{from_dt.strftime('%B %d, %Y')} to {to_dt.strftime('%B %d, %Y')}*\n\n"
    )
    post_to_slack(DEST_CHANNEL_ID, header + report)
    print("Done!")


def _parse_args():
    parser = argparse.ArgumentParser(description="Generate and post a company intelligence digest.")
    parser.add_argument("--from", dest="from_date", help="Start date, MM-DD-YYYY, inclusive. Defaults to 7 days before --to (or before now).")
    parser.add_argument("--to", dest="to_date", help="End date, MM-DD-YYYY, inclusive. Defaults to now.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_digest(args.from_date, args.to_date)