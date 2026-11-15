#!/usr/bin/env python3
"""Exercise the company-intelligence-report skill in isolation.

Runs the same Claude API call digest.py makes (skill + code_execution +
web_search) against a transcript you supply, without touching Slack at all
(no fetching #01-raw-intel, no posting to Slack). Prints the generated
report and validates it with company-intelligence-report/scripts/validate_output.py.

Usage:
    python test_skill.py --sample
    python test_skill.py --file transcript.txt
    python test_skill.py --text "[2026-07-01 09:14] check out Acme Robotics..."
    cat transcript.txt | python test_skill.py
    python test_skill.py --sample --publish   # push local SKILL.md edits as a
                                               # new draft version, then test it

Requires env vars ANTHROPIC_API_KEY and COMPANY_SKILL_ID (or pass --skill-id).
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

import anthropic
from dotenv import load_dotenv

load_dotenv()

SKILL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "company-intelligence-report")
VALIDATOR = os.path.join(SKILL_DIR, "scripts", "validate_output.py")

ALLOWED_DOMAINS = [
    "sec.gov",
    "efts.sec.gov",
    "federalreserve.gov",
    "crunchbase.com",
    "finance.yahoo.com",
    "linkedin.com",
]

SAMPLE_TRANSCRIPT = "SpaceX, Databricks, SambaNova"

_ENTRY_START_RE = re.compile(r"^\*(<https?://\S+\|[^>]+>|[^*]+)\*:")


def _strip_preamble(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "---" or _ENTRY_START_RE.match(stripped):
            return "\n".join(lines[i:]).strip()
    return text.strip()


def publish_draft_version(client, skill_id):
    from anthropic.lib import files_from_dir

    version = client.beta.skills.versions.create(
        skill_id=skill_id,
        files=files_from_dir(SKILL_DIR),
        betas=["skills-2025-10-02"],
    )
    print(f"Published local SKILL.md as version {version.version} of skill {skill_id}")
    return version.version


def generate_company_report(client, skill_id, version, raw_conversation, model, max_continuations):
    messages = [
        {
            "role": "user",
            "content": (
                "Generate this week's company intelligence report from the "
                "following Slack messages (past 7 days):\n---\n"
                f"{raw_conversation}\n---"
            ),
        }
    ]
    common = dict(
        model=model,
        max_tokens=8192,
        betas=["code-execution-2025-08-25", "skills-2025-10-02"],
        tools=[
            {"type": "code_execution_20250825", "name": "code_execution"},
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "allowed_domains": ALLOWED_DOMAINS,
            },
        ],
    )

    response = client.beta.messages.create(
        container={"skills": [{"type": "custom", "skill_id": skill_id, "version": version}]},
        messages=messages,
        **common,
    )

    container_id = response.container.id
    for _ in range(max_continuations):
        if response.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": response.content})
        response = client.beta.messages.create(
            container={
                "id": container_id,
                "skills": [{"type": "custom", "skill_id": skill_id, "version": version}],
            },
            messages=messages,
            **common,
        )
    else:
        print(
            f"Warning: hit max continuations ({max_continuations}) while stop_reason "
            "is still 'pause_turn'; report may be incomplete.",
            file=sys.stderr,
        )

    raw_text = "\n".join(block.text for block in response.content if block.type == "text")
    return _strip_preamble(raw_text), response


def read_transcript(args, parser):
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            return f.read()
    if args.text:
        return args.text
    if args.sample:
        return SAMPLE_TRANSCRIPT
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data
    parser.error("Provide --file, --text, or --sample, or pipe a transcript via stdin.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--file", help="Path to a text file with the raw transcript")
    src.add_argument("--text", help="Transcript text given directly on the command line")
    src.add_argument("--sample", action="store_true", help="Use a small built-in sample transcript")
    parser.add_argument(
        "--skill-id", default=os.environ.get("COMPANY_SKILL_ID"),
        help="Skill ID to test (default: $COMPANY_SKILL_ID)",
    )
    parser.add_argument("--version", default="latest", help="Skill version to test (default: latest)")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Model to use")
    parser.add_argument(
        "--publish", action="store_true",
        help="Publish company-intelligence-report/ as a new draft skill version before testing it",
    )
    parser.add_argument("--out", help="Write the generated report to this file")
    parser.add_argument("--no-validate", action="store_true", help="Skip running validate_output.py")
    parser.add_argument("--max-continuations", type=int, default=20)
    args = parser.parse_args()

    transcript = read_transcript(args, parser)

    if not args.skill_id:
        parser.error("No skill id given. Pass --skill-id or set COMPANY_SKILL_ID.")

    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY

    version = args.version
    if args.publish:
        version = publish_draft_version(client, args.skill_id)

    print(f"Running skill {args.skill_id} (version={version}) against transcript ({len(transcript)} chars)...")
    report, _response = generate_company_report(
        client, args.skill_id, version, transcript, args.model, args.max_continuations
    )

    print("\n----- REPORT -----\n")
    print(report)
    print("\n-------------------")

    report_path = args.out
    cleanup = False
    if not report_path:
        fd, report_path = tempfile.mkstemp(prefix="skill_test_report_", suffix=".txt")
        os.close(fd)
        cleanup = True
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    if args.out:
        print(f"Saved report to {report_path}")

    if args.no_validate:
        return

    result = subprocess.run([sys.executable, VALIDATOR, report_path])
    if cleanup:
        os.remove(report_path)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
