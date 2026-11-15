#!/usr/bin/env python3
"""Validate a company intelligence report against the output contract.

Usage: python validate_output.py report.txt [dispositions.txt]

Exits 0 if valid; prints violations and exits 1 otherwise.

If a dispositions file is given (see SKILL.md step 2), each line must read
`N. Name — included` or `N. Name — excluded: reason`. Every `excluded` line
must carry a reason, and every `included` candidate must actually appear
somewhere in the report — this is what makes silent drops impossible.
"""
import re
import sys

DISPOSITION_RE = re.compile(
    r"^\s*\d+\.\s*(.+?)\s*[—-]\s*(included|excluded)\s*(?::\s*(.+))?\s*$",
    re.IGNORECASE,
)


def validate(text: str) -> list[str]:
    problems = []
    stripped = text.strip()

    if stripped == "No companies were mentioned in this week's messages.":
        return []

    lines = stripped.splitlines()

    # Preamble / postamble
    if lines and not (lines[0].startswith("*<") or lines[0].startswith("*")):
        problems.append(f"First line is not a company entry: {lines[0]!r}")

    entry_pattern = re.compile(r"^\*(<https?://\S+\|[^>]+>|[^*]+)\*: .+")

    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s:
            continue
        if s == "---":
            continue
        if s.startswith(";"):
            problems.append(f"Line {i} starts with a semicolon: {s[:60]!r}")
        if not entry_pattern.match(s):
            problems.append(f"Line {i} is not a valid single-line entry or separator: {s[:80]!r}")
        if re.search(r"~[^~]+~", s):
            problems.append(f"Line {i} contains strikethrough: {s[:60]!r}")
        if re.search(r"</?[a-zA-Z][^>]*>", s.replace("*<", "").split("|")[0]) and "<http" not in s:
            problems.append(f"Line {i} may contain HTML/XML tags: {s[:60]!r}")
        if re.search(r"\b(was|previously|formerly)\b.*\bnow\b", s, re.IGNORECASE):
            problems.append(f"Line {i} shows superseded-value comparison: {s[:80]!r}")
        if re.search(r"\((estimated|approx\.?|roughly)\)", s, re.IGNORECASE):
            problems.append(f"Line {i} contains an estimated figure: {s[:80]!r}")
        if re.search(r'["“”]', s):
            problems.append(f"Line {i} contains a verbatim quote, paraphrase it: {s[:80]!r}")
        if s.count("; ") > 5:
            problems.append(f"Line {i} has more than 5 facts")
        if s.endswith("."):
            problems.append(f"Line {i} ends with a period: {s[:80]!r}")
        if re.search(
            r"\b(now|currently|recently|yesterday|today|tomorrow|this week|next week|last week|this month|next month|last month|in a few weeks)\b",
            s,
            re.IGNORECASE,
        ):
            problems.append(f"Line {i} contains an unresolved relative time marker (convert to a calendar date per SKILL.md step 4): {s[:80]!r}")

    # Blank line + --- separation between entries
    entries = [l for l in lines if l.strip() and l.strip() != "---"]
    seps = [l for l in lines if l.strip() == "---"]
    if len(entries) > 1 and len(seps) < len(entries) - 1:
        problems.append("Entries are not all separated by '---' lines")

    return problems


def parse_dispositions(text):
    """Parse a step-2 scratch file dispositioning every extracted candidate.

    Returns a list of {"name", "status", "reason"} dicts; lines that don't
    match the expected `N. Name — included|excluded[: reason]` shape are
    skipped.
    """
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = DISPOSITION_RE.match(line)
        if not m:
            continue
        name, status, reason = m.groups()
        entries.append({
            "name": name.strip(),
            "status": status.lower(),
            "reason": reason.strip() if reason else None,
        })
    return entries


def validate_dispositions(report_text, disposition_text):
    problems = []
    entries = parse_dispositions(disposition_text)
    if not entries:
        problems.append("Dispositions file has no parseable candidate lines.")
        return problems

    report_lower = report_text.lower()
    for entry in entries:
        if entry["status"] == "excluded":
            if not entry["reason"]:
                problems.append(
                    f"Candidate {entry['name']!r} excluded without a reason"
                )
        elif entry["name"].lower() not in report_lower:
            problems.append(
                f"Candidate {entry['name']!r} marked included but not found in report"
            )
    return problems


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        content = f.read()
    issues = validate(content)
    if len(sys.argv) == 3:
        with open(sys.argv[2], encoding="utf-8") as f:
            disposition_text = f.read()
        issues += validate_dispositions(content, disposition_text)
    if issues:
        print("INVALID report:")
        for p in issues:
            print(f"  - {p}")
        sys.exit(1)
    print("Report is valid.")
