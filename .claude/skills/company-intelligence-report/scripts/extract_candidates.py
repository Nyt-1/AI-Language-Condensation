#!/usr/bin/env python3
"""Extract company-name candidates from a raw Slack transcript.

Usage: python extract_candidates.py transcript.txt

Not every message uses the "Name: facts" pattern. Some are just a pasted,
colon-free list of company names (often with sector-header lines mixed in,
e.g. "Robotics" followed by "Waymo" / "Physical Intelligence" / ...), and
some are prose with an inline comma/"and"-separated list of names dropped
into a sentence (e.g. "thinking to do Skydio, Sierra and Periodic"). This
script runs four mechanical passes and prints one merged, numbered
candidate list, in first-seen order:

1. Colon-delimited: a capitalized word/phrase immediately followed by a
   colon (the "Name: facts" pattern).
2. Inline list: a run of 2+ comma/"/"-/"and"-joined capitalized phrases
   anywhere in the text, e.g. "Skydio, Sierra and Periodic" or
   "Canva / Notion".
3. Bare line: an entire short line with no colon, taken whole as one
   candidate (e.g. "Anthropic", "Kobold metals", "Applied compute"). This
   is the dominant shape in a pasted, one-company-per-line list. It also
   catches sector-header lines ("Robotics", "Consumer", "AI native
   vertical") — those aren't companies, but flagging them costs nothing
   since step 2 disposes every candidate anyway, and silently filtering
   them mechanically risks a real company slipping through the same filter.
4. No-context: a capitalized word/phrase that shows up mid-sentence with
   nothing explaining it, not already caught above — tagged
   "(no context — verify)". An unexplained capitalized word dropped into a
   sentence is very often a company name-drop, and this is also how an
   unnamed company gets surfaced (e.g. "grabbed coffee with Sid's company"
   flags "Sid" even though there's no proper company name yet).

This is a mechanical floor, not a source of truth: it cannot catch a
company name typed in lowercase in a casual aside (e.g. "Harvey and legora
but I'm more eh now" — "legora" is really Legora), and it cannot tell a
sector header apart from a company on its own. Every candidate — from any
pass — still needs a judgment call in step 2 (candidate reconciliation);
being on this list is not itself proof something is a company. See
SKILL.md step 2.
"""
import re
import sys

TIMESTAMP_PREFIX_RE = re.compile(r"^\[[^\]]+\]\s*")

NAME_COLON_RE = re.compile(
    r"([A-Z][A-Za-z0-9&.'/]*(?:\s+[A-Z&][A-Za-z0-9&.'/]*){0,3}):(?!\d)"
)

# A capitalized phrase, up to 4 words, where later words may be lowercase
# (covers names like "Kobold metals", "Applied compute"). Never lets "and"
# itself become part of the phrase, so list-splitting works correctly. No
# "." in the char class — otherwise a trailing sentence period fuses onto
# the last list item and drags the next sentence's words in behind it.
_TOKEN = r"[A-Z][A-Za-z0-9&'-]*(?:\s+(?!and\b)[A-Za-z][A-Za-z0-9&'-]*){0,3}"

LIST_RUN_RE = re.compile(
    _TOKEN
    + r"(?:\s*(?:,|/)\s*(?:and\s+)?" + _TOKEN + r"|\s+and\s+" + _TOKEN + r"){1,}"
)
LIST_SPLIT_RE = re.compile(r"\s*(?:,|/)\s*(?:and\s+)?|\s+and\s+")

BARE_PHRASE_RE = re.compile(r"\b([A-Z][a-zA-Z]{1,})\b")
SENTENCE_BOUNDARY_RE = re.compile(r"[.!?:;\-—]\s*$")
INLINE_STOPWORDS = {"i"}

SENTENCE_END_CHARS = (".", "?", "!")
MAX_BARE_LINE_WORDS = 6


def _strip_line(raw_line):
    return TIMESTAMP_PREFIX_RE.sub("", raw_line)


def extract_colon(text):
    candidates = []
    seen = set()
    for raw_line in text.splitlines():
        line = _strip_line(raw_line)
        for match in NAME_COLON_RE.finditer(line):
            name = match.group(1).strip()
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(name)
    return candidates


def extract_list_items(text):
    candidates = []
    seen = set()
    for raw_line in text.splitlines():
        line = _strip_line(raw_line)
        for run_match in LIST_RUN_RE.finditer(line):
            for token in LIST_SPLIT_RE.split(run_match.group(0)):
                name = token.strip()
                if not name:
                    continue
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(name)
    return candidates


def _looks_like_full_sentence(line):
    if len(line.split()) > MAX_BARE_LINE_WORDS:
        return True
    return line.rstrip().endswith(SENTENCE_END_CHARS)


def extract_bare_lines(text, already_found):
    seen = {name.lower() for name in already_found}
    candidates = []
    for raw_line in text.splitlines():
        line = _strip_line(raw_line).strip()
        if not line or ":" in line:
            continue
        if _looks_like_full_sentence(line):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(line)
    return candidates


def extract_no_context(text, already_found):
    seen = {name.lower() for name in already_found}
    for name in already_found:
        for word in name.split():
            seen.add(word.lower())
    flagged = []
    for raw_line in text.splitlines():
        line = _strip_line(raw_line)
        for match in BARE_PHRASE_RE.finditer(line):
            word = match.group(1)
            key = word.lower()
            if key in seen or key in INLINE_STOPWORDS:
                continue
            prefix = line[: match.start()]
            if SENTENCE_BOUNDARY_RE.search(prefix):
                continue
            seen.add(key)
            flagged.append(word)
    return flagged


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        transcript = f.read()

    colon_candidates = extract_colon(transcript)
    list_candidates = extract_list_items(transcript)
    bare_line_candidates = extract_bare_lines(
        transcript, colon_candidates + list_candidates
    )
    no_context_candidates = extract_no_context(
        transcript, colon_candidates + list_candidates + bare_line_candidates
    )

    all_candidates = (
        colon_candidates
        + list_candidates
        + bare_line_candidates
        + [f"{w} (no context — verify)" for w in no_context_candidates]
    )

    if not all_candidates:
        print("No candidates found.")
    else:
        for i, name in enumerate(all_candidates, 1):
            print(f"{i}. {name}")
