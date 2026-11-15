---
name: deal-pipeline-digest
description: Generate a deal pipeline digest from Slack messages that mix informational updates and directional asks. Use this skill whenever asked to analyze a Slack transcript where each message is either informational (states a fact worth recording about a company/deal) or directional (requests or assigns an action, without itself stating a new fact), and the requested output is deal-pipeline entries in Slack mrkdwn format with an owner tag. Always use this skill when the input is a block of timestamped, sender-tagged Slack messages from a deal-pipeline channel.
---

# Deal Pipeline Digest
Turn a block of Slack messages from a deal-pipeline channel into a compact,
Slack-ready digest: one line per company/deal, verified facts only, tagged
with who owns it.

## Step 0: Classify informational vs directional

Every message in the transcript is one of two things:

- **Informational** — it states at least one fact worth recording about a
  company or deal (a data point, a status change, a meeting recap, news, an
  intro that happened). Only informational content produces a report entry.
- **Directional** — it requests, assigns, or directs an action rather than
  stating a fact (e.g. "can someone follow up with Acme", "let's pass on
  this", "put this on hold", "send me the deck"). A purely directional
  message never produces its own entry.

Classify per **sentence**, extract the fact and read the directional part for
who's leading the deal (see Step 2). A message with no fact at all — pure
logistics or a request — is skipped entirely; it never becomes a report
entry, no matter how company-specific it sounds.

## Workflow

1. **Generate a candidate list mechanically — don't rely on reading alone.**
   Exhaustive enumeration over a dense transcript is exactly where reading
   comprehension silently drops entries. Write the raw transcript to a file
   (e.g. `transcript.txt`) and run
   `scripts/extract_candidates.py transcript.txt`. Run this over the whole
   transcript, not just messages you've already judged informational — a
   candidate can still turn out to belong to a directional-only message and
   get excluded in step 2. Treat the printed list as a mechanical floor, not
   the full answer: every candidate still needs a judgment call next, and a
   candidate not on the list can still turn out to be real (a name typed in
   lowercase, a description-only reference to an unnamed company).

2. **Reconcile every candidate, message by message.** Each line of the
   transcript is one Slack message, prefixed with its `[timestamp]` and the
   sender's name. Go through the transcript one message at a time, in order:
   - Apply Step 0: does this message contain a fact?
   - For a message that does contain a fact, extract the company (or the
     description of an unnamed company, e.g. "Sid's company" if no proper
     name is ever given) and every fact it states.
   - Generic-sounding names are still companies when followed by a colon and
     deal facts — never discard a candidate because the name is a common
     word.
   - Watch for company names typed in lowercase in a casual aside, and for
     names buried mid-sentence without a colon — the mechanical script only
     catches capitalized, colon-delimited, or list-shaped mentions.
   - **Order entries by each company's most recent mention.** If a company
     comes up more than once across the transcript, its entry belongs at the
     position of its LAST mention, not its first — as if the earlier mention
     were deleted and a new entry written where the later one occurs.
   In a scratch file — not part of the final report — write a disposition
   for every company candidate: `N. Name — included` or `N. Name — excluded: reason`.
   Every candidate must be dispositioned before you draft the report.

3. **Determine the deal owner for each entry**
  Determine the deal owner for each entry For each company, list the people who sent the messages, and the people who are mentioned in the messages pertaining to the company. Sort them in order of mentions: being the poster counts as being mentioned twice. The deal owner is either the priorty name with the most mentions, or the name with the most mentions.
   
4. **Collect facts.**
   Facts stated in the Slack messages themselves may be used and should be
   prioritized. Numbers and growth statistics should always be included.
   Quoted text in a message (single or double quotes) is usually something
   said directly to us, not a company fact in itself — paraphrase it into a
   short indirect-speech fact instead of copying it verbatim (e.g. a message
   containing `'do you want in'` becomes `asked if we wanted in`).
   **Resolve every relative time marker to a calendar date.** Each
   transcript line is prefixed with that message's own `[YYYY-MM-DD HH:MM]`
   timestamp — anchor relative language ("now", "this week", "last week",
   "recently", "yesterday", "next month", etc.) to *that* timestamp, never
   to today's date. Use a short `Mon D` or `week of Mon D` format.
   **DO NOT DO EXTERNAL RESEARCH for facts.** Only use facts stated in the
   transcript — never pull figures, growth, or descriptions from web search
   or outside knowledge. The one exception: use web search to find each
   company's **official website URL**, for the link in the company name and
   to verify it's a real company. Never use web search results to add or
   change facts.

   **Finding the website — search with disambiguating context, then
   verify before trusting it:**
   - Don't search the bare company name alone, especially for a
     generic-sounding name or common word (`Hooli Nuclear`, `Eon`, `K2`) —
     it collides with unrelated companies, places, and dictionary words.
     Pull context straight from the transcript to narrow the search:
     industry/what it does, founder or CEO name if mentioned, funding
     round, HQ city (e.g. `"Hooli Nuclear" AI nuclear reactors` instead of
     just `Hooli Nuclear`). A founder/CEO name is often the single
     strongest disambiguator for an early-stage company with little web
     presence.
   - If the first query doesn't surface a clear candidate, try at least
     one differently-worded query before concluding there's no website —
     one failed search is not proof the company has no site.
   - **Verify before accepting**: fetch the candidate URL with `web_fetch`
     and confirm the page actually matches what the transcript says about
     the company (same industry/product, same rough stage). A same-named
     but unrelated business, a parked domain, or a disambiguation/listing
     page is not a match, even if it ranked first.
   - Prefer the company's own root domain (`.com`, `.ai`, `.io`, etc.) over
     a LinkedIn, Crunchbase, or news-article page about it — those
     describe the company, they aren't the company's own site.
   - If no candidate can be verified this way, include the entry unlinked
     (`*Company Name*:`) rather than guessing — a wrong link is worse than
     no link.
   Images and links included in the transcript are transcript content, not
   external research — read them the same way you'd read a quote, per the
   photo-link-reader skill, and fold any facts they carry into the same
   candidate list.

5. **Write the report** using the output contract below.

6. **Validate before answering.** Write your drafted report to a file and
   run `scripts/validate_output.py report.txt dispositions.txt` (the
   dispositions file from step 2). It checks output formatting, the owner
   tag, AND cross-checks that every candidate marked `included` actually
   appears in the report and that every `excluded` candidate has a reason.
   If it reports violations, fix them and re-run until it passes. Only
   return a report that passes validation.

## Hard rules on facts
- NEVER invent, estimate, or extrapolate figures.
- If a figure has been updated or superseded, include the change as a
  growth statistic.
- Keep every fact — never drop or generalize away a detail for brevity.
  But phrase each one tersely: cut filler words, hedging, and connective
  narration ("additionally", "furthermore", "the company also noted",
  etc.) — state facts as compact fragments joined by semicolons, not
  full sentences. Brief wording, not less content.
- Maximum 700 characters per company (not counting the `[Owner: ...]` tag).
- NEVER leave a relative time marker ("now", "this week", "recently", etc.)
  in the output — convert it to a calendar date/week using that message's
  own timestamp.
- Every entry must carry an `[Owner: ...]` tag

## Output contract

Output ONLY company lines and `---` separators. No introduction, no
headers, no closing remarks, no XML/citation/HTML tags, no ending period.

Each entry is exactly ONE line, in Slack mrkdwn and **at most 250
characters** (excluding the owner tag):

```
*<https://company-website.com|Company Name>*: one-line description; fact; fact [Owner: Alice]
```

- The line starts with the bold linked name `*<url|Name>*:` — if no
  official website was found, use `*Company Name*:` without a link.
- Facts are joined by semicolons on the same line. A semicolon must never
  start a line.
- No entry ever ends with a period before the owner tag.
- **No line breaks inside an entry, ever.**
- No markdown inside facts: no strikethrough, no bold/italics beyond the
  company-name link, no bullet points, no labels or subtitles.
- No quotation marks inside facts — paraphrase quoted speech into indirect
  speech instead of quoting it.
- The line ends with an owner tag, `[Owner: Name]`.
- Separate entries with a blank line followed by `---` on its own line.
- If no informational messages are found in the transcript, output exactly:
  `No informational messages were found in this transcript.`

## Worked example (FORMAT ONLY — never output this company unless it appears in the transcript)

Transcript:
```
[2026-07-20 09:14] (Jane Doe): talking to Hooli Nuclear (AI for nuclear operators) — they just closed $5M from Massive Dynamic on an uncapped SAFE.
[2026-07-20 09:20] (Bob Smith): Alice, send me the deck for Globex.
```

becomes:
```
*<https://hooli-nuclear.example|Hooli Nuclear>*: AI for nuclear operators; closed $5M from Massive Dynamic on uncapped SAFE [Owner: Jane]
```

Correct format:
```
*<https://acme-robotics.example|Acme Robotics>*: warehouse automation; raising a $10M seed [Owner: Bob]
---


Incorrect (do not do any of these):
```
Here are the deals mentioned this week:                     ← forbidden preamble
*<https://acme.example|Acme>*: warehouse automation          ← missing owner tag
raising a $10M seed [Owner: Jane]                              ← line break inside entry
*Initech*: closing round next week [Owner: Alice]             ← unresolved relative time marker
```