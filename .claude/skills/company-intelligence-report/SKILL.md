---
name: company-intelligence-report
description: Generate a weekly company intelligence report from #01-raw-intel messages. Use this skill whenever asked to analyze Slack conversation transcripts for company or startup mentions, research those companies, or produce a company digest, weekly report, or investment-channel summary. Always use this skill when the input is a block of timestamped Slack messages and the requested output is company facts in Slack mrkdwn format.
---

# Company Intelligence Report
Turn one week of raw Slack messages from an investment channel into a compact,
Slack-ready digest: one line per company, verified facts only.

## Workflow

1. **Generate a candidate list mechanically — don't rely on reading alone.**
   Exhaustive enumeration over a dense transcript is exactly where reading
   comprehension silently drops entries. Write the raw transcript to a file
   (e.g. `transcript.txt`) and run
   `scripts/extract_candidates.py transcript.txt`. Not every message uses
   the `Name: facts` pattern — some are a pasted, colon-free list of bare
   company names (one per line, often with sector-header lines like
   "Robotics" or "Consumer" mixed in), and some are prose with an inline
   comma/"and"-separated list dropped into a sentence (e.g. "thinking to do
   Skydio, Sierra and Periodic"). The script runs four passes and prints one
   merged, numbered list:
   - **Colon-delimited** — the dominant `Name: facts` pattern.
   - **Inline list** — a run of 2+ comma/"/"-/"and"-joined capitalized
     phrases anywhere in the text (`Skydio, Sierra and Periodic`,
     `Canva / Notion`).
   - **Bare line** — a whole short line with no colon, taken as one
     candidate verbatim (`Anthropic`, `Kobold metals`). This is the
     dominant shape in a pasted one-company-per-line list, and it also
     flags sector-header lines (`Robotics`, `AI native vertical`) since the
     script can't reliably tell a header apart from a company — that
     distinction is a step-2 judgment call, not a step-1 filter.
   - **No context** — a capitalized word/phrase that shows up mid-sentence
     with nothing explaining it, tagged `(no context — verify)`. This is
     also how an **unnamed company** gets surfaced: if a message only
     describes a company through its founder ("grabbed coffee with Sid's
     company"), the person's name (`Sid`) gets flagged here even though
     there's no proper company name yet.
   Treat the whole list as a mechanical floor, not the full answer — it
   cannot catch a company name typed in lowercase in a casual aside (e.g.
   "Harvey and legora" — "legora" is really Legora), and it will still miss
   forwarded/pasted blurbs. Every candidate — from any pass — still needs a
   judgment call in step 2; being on the list is not itself proof it's a
   company.
2. **Reconcile every candidate, message by message.** Each line of the
   transcript is one Slack message, prefixed with its `[timestamp]`. Go
   through the transcript one message at a time, in order, and parse each
   message on its own before moving to the next — do not skim the transcript
   as a single block, and do not stop early once a pass "looks complete."
   For each message:
   - Check off every script-generated candidate that appears in it.
   - A single message may contain a bulleted or line-separated list of
     companies (often under headers like Seed / Series A / Early Growth).
     Every bullet in the form `Name: facts` is a separate company entry —
     extract all of them, and count the bullets to confirm none were
     skipped. If a message has more than ~5 candidates, process it in
     batches of 5 rather than all at once — recall drops on long lists
     processed in one pass.
   - Generic-sounding names (Actor, Takeoff, Eon) are still companies when
     followed by a colon and deal facts — never discard a candidate because
     the name is a common word.
   - **A bare-list message (no colons at all) needs its own pass.** When a
     message is just a pasted list of names, go line by line and sort each
     line into one of two buckets: an actual company name (include it,
     however short or generic it looks — `K2`, `Mach`, `Eon` are all real
     companies here), or a sector/category header that just groups the
     company lines beneath it (`Robotics`, `Consumer`, `AI native vertical`,
     `Data platforms poised to benefit from AI`) — exclude headers with
     reason "section header, not a company." A header usually (a) reads as
     a generic sector/theme rather than a brand, and (b) is immediately
     followed by a run of lines that are clearly company names in that
     theme. When genuinely unsure which bucket a short line belongs in,
     default to including it — an extra header line in the report draft is
     caught at validation, but a dropped company is not.
   - **Unnamed companies count as companies.** If a message only describes
     a company through its founder/owner and never gives it a proper name
     (e.g. "grabbed coffee with Sid's company" or "Sidd Srinivas robotics
     company"), still include it — use the description itself as the name
     (`Sid's company`, `Sidd Srinivas robotics company`), unlinked (no
     website search will resolve a name that doesn't exist yet). Don't wait
     for a real name to show up later in the transcript; if one never does,
     the description is the name.
   - **Every `(no context — verify)` candidate gets checked, not dismissed.**
     These are capitalized words/phrases the script found mid-sentence with
     nothing explaining them. Most will turn out to be genuine but
     easy-to-miss company name-drops; some will be people's names, places,
     or filler words that aren't companies. Read the message it came from
     and decide — default assumption going in should be "probably a
     company," not "probably noise."
   - **Watch for company names typed in lowercase.** The regex passes only
     catch capitalized names, so a casual aside like "Harvey and legora but
     I'm more eh now" won't get "legora" (really Legora) flagged
     mechanically at all. When a sentence name-drops a well-known company
     alongside an unfamiliar lowercase word in the same breath, check
     whether that lowercase word is itself a company you don't recognize
     the capitalization convention for.
   - Also look for what the regex passes can't catch: names buried
     mid-sentence without a colon (e.g. `...grabbed coffee with an old
     friend - Overlap: raised a seed round...` — note this one does have a
     colon and would be caught, but not every buried mention will) and
     forwarded pitches or blurbs pasted into a message (often after a
     divider).
   - **Order entries by each company's most recent mention.** If a company
     comes up more than once across the transcript, its entry belongs at the
     position of its LAST mention, not its first — as if the earlier mention
     were deleted and a new entry written where the later one occurs.
   In a scratch file — not part of the final report — write a disposition
   for every item: every script-generated candidate, plus every company you
   found by hand. One line each:
   `N. Name — included` or `N. Name — excluded: reason`.
   Every item must be dispositioned before you draft the report; a company
   can only be left out if you wrote down why (this file is checked in
   step 6).
3. **DO NOT DO EXTERNAL RESEARCH for facts.** Only use facts stated in the
   transcript — never pull figures, growth, or descriptions from web search or
   outside knowledge. The one exception: use web search to find each
   company's **official website URL**, for the link in the company name and to verify if it's a real company
   Never use web search results to add or change facts. If web search cannot
   confirm the company or find a website, still include the entry without a
   link — verification failure is never a reason to omit a company mentioned
   in the transcript.
4. **Collect facts.** 
   Facts stated in the Slack messages themselves may also be used and should be priortized. Numbers and growth statistics should always be included.
   Quoted text in a message (single or double quotes) is usually something
   said directly to us, not a company fact in itself — paraphrase it into a
   short indirect-speech fact instead of copying it verbatim. Strip the
   quotation marks and first-person phrasing; report it in third person from
   the company/contact's perspective. Example: a message containing
   `'do you want in'` becomes `asked if we wanted in`, not a quoted fact. 
   **Resolve every relative time marker to a calendar date.** Each transcript
   line is prefixed with that message's own `[YYYY-MM-DD HH:MM]` timestamp —
   anchor relative language to *that* timestamp, never to today's date or
   report-generation time. Words like "now", "currently", "this week",
   "next week", "last week", "in a few weeks", "recently", "yesterday",
   "next month" must be rewritten as a concrete date or week, computed from
   the message's own timestamp:
   - A message timestamped `2026-07-06` saying "raising now" →
     "raising as of Jul 6".
   - A message timestamped `2026-07-06` saying "closing next week" →
     "closing week of Jul 13".
   - A message timestamped `2026-06-20` saying "talked last week" →
     "talked week of Jun 15".
   Use a short `Mon D` or `week of Mon D` format (add the year only if it
   would otherwise be ambiguous, e.g. near a year boundary) to stay within
   the character limit. This matters because reports get merged across
   weeks (see `log.py`) — an untranslated "now" written in one week's
   message would otherwise read as current several weeks later, when it
   actually describes a moment three weeks in the past.
5. **Write the report** using the output contract below.
6. **Validate before answering.** Write your drafted report to a file and
   run `scripts/validate_output.py report.txt dispositions.txt` (the
   dispositions file from step 2). It checks output formatting AND
   cross-checks that every candidate marked `included` actually appears in
   the report and that every `excluded` candidate has a reason — so a
   company can't be silently dropped. If it reports violations, fix them and
   re-run until it passes. Only return a report that passes validation.

## Hard rules on facts
- NEVER invent, estimate, or extrapolate figures.
- If a figure has been updated or superseded, include the change as a growth statistic. 
- Keep every fact — never drop or generalize away a detail for brevity.
  But phrase each one tersely: cut filler words, hedging, and connective
  narration ("additionally", "furthermore", "the company also noted",
  etc.) — state facts as compact fragments joined by semicolons, not
  full sentences. Brief wording, not less content.
- Maximum 250 characters per company.
- NEVER leave a relative time marker ("now", "this week", "next week", "recently", "yesterday", etc.) in the output — convert it to a calendar date/week using that message's own timestamp, per step 4.

## Output contract

Output ONLY company lines and `---` separators. No introduction ("Here are
the companies..."), no headers, no closing remarks, no XML/citation/HTML
tags, no ending period.

Each company entry is exactly ONE line, in Slack mrkdwn and **at most 250 character**:

```
*<https://company-website.com|Company Name>*: one-line description; fact; fact; fact.
```

- The line starts with the bold linked name `*<url|Name>*:` — if no official
  website was found, use `*Company Name*:` without a link.
- Facts are joined by semicolons on the same line. A semicolon must never start a line. 
- No entry ever ends with a period. Drop any trailing period from the last fact on the line.
- **No line breaks inside an entry, ever.**
- No markdown inside facts: no `~strikethrough~`, no bold/italics beyond the
  company-name link, no bullet points, no labels or subtitles.
- No quotation marks inside facts — paraphrase quoted speech into indirect
  speech instead of quoting it.
- Separate companies with a blank line followed by `---` on its own line.
- If no companies are mentioned in the transcript, output exactly:
  `No companies were mentioned in this week's messages.`

## Worked examples (FORMAT ONLY — never output these companies unless they appear in the transcript)

Example:
'''
Hooli Nuclear (AI for nuclear operators, reactors) - dinner last night. Raised opportunistic $5M from Massive Dynamic on uncapped safe w/ 10-20% discount. Investor convos in July, likely Series A in September. Assembled impressive group of nuclear leaders around the table - a utility CNO, a federal energy official, two plant operators, an industry trade group EVP (strong partner to have) - stay close to Hooli, follow up with attendees. $9M EBITDA in 2025 to $20M 2026.
'''

becomes

'''
Hooli Nuclear (AI for nuclear ops) raised $5M from Massive Dynamic (uncapped SAFE, 10–20% disc); $9M EBITDA to $20M EBITDA (2025-2026); Series A likely Sept, investor convos July. Strong nuclear leadership at dinner (utility, federal, trade group) — stay close, follow up w/ attendees
'''


Correct Format:
'''
Pied Piper: AI compression; Raising A at $90M post; Raviga leading
---
Stark Avionics: Raising $35M; $20M contract signed w/ Wayne Logistics; two firms competing for lead
---
Telemetrix: Raising $10–15M; Bluebird Capital leading
---
Lumen Labs: Personal AI for visual media; RR $600K; $15M raise
---
Photonix: Photonic circuits for quantum computing; $25M Series A; $2M RR LTM
---
Vector Storage: GPU-native storage infra; $50–60M; ~$37M RR in 2025
---
Parcel AI: Real estate data AI; $200K+ ACV; customers include a port authority, several universities and hospital systems; comp question vs. two construction-doc competitors (which sell to GCs)
---
Andes Pay: LatAm stables; $10M RR; 2x YoY; $40M Series B
---
Sightline: Computer vision for manufacturing analytics; raising bridge at $63M pre; two existing investors participating

```

Incorrect (do not do any of these):

```
Here are the companies mentioned this week:          ← forbidden preamble

*<https://acme-robotics.example|Acme Robotics>*:
warehouse automation startup                          ← line break inside entry
; $18M ARR;                                           ← semicolon starting a line
Series B                                              ← new line break

*Northwind Analytics*: ARR ~$5M (estimated)           ← invented/estimated figure
*Contoso*: revenue was $10M, now $25M                 ← superseded-value comparison
*Fabrikam*: ~$12M~ $20M ARR                           ← strikethrough
*Initech*: founder said "do you want in"              ← verbatim quote, not paraphrased
*Umbrella Corp*: raising $30M Series B.               ← unwanted trailing period
*Vandelay Industries*: closing round next week        ← relative time marker left unresolved; should be "closing round week of <date computed from the message's own timestamp>"
```
