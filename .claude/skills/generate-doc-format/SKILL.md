---
name: generate-doc-format
description: Format a JSON array of company/deal rows exported from Coda (fields keyed by column name such as "Company", "Summary", "Website", "Deal Funnel", "Assignee", "Category") into a grouped, bulleted Markdown report section. Use whenever asked to turn Coda deal-row data into this document's current-pipeline or weekly-history section format.
---

# Generate Doc Format

Turn a JSON array of company/deal rows into a Markdown report section: grouped by Deal Funnel, one bullet per company. The output of this report is rendered into a Word document (.docx).

## Input

You'll be given a JSON array of rows, each an object keyed by column name (exact keys vary — commonly "Company", "Summary", "Website", "Deal Funnel", "Assignee", "Category").

## Workflow

### Current Pipeline report

Grab all companies mentioned in the Coda table and use the most recent entry for each. Create a general report with those entries, organized by Deal Funnel. Input all the entries in the order that they appear in the Coda table — so if A is above B in the Coda table, A will be above B in the report, assuming they're in the same Deal Funnel.

### Weekly History report

Grab all companies mentioned in the Coda table and create a separate report for every date. No need to check for duplicate entries for a company since that is already taken care of, but organize by funnel and also sort by order as they appear in Coda the same way as the Current Pipeline report. Keep these reports separate on different pages, in descending order so the most recent date comes first after the general report.

## Output format

Do not output a heading — go straight into the report.

1. Group every row by its Deal Funnel value. Give each distinct funnel its own section, introduced by a level-2 Markdown heading (##) naming that funnel. If a row has no Deal Funnel value, group it under a section titled "Unspecified".
2. Within each section, list every row as exactly one bullet point:
   - `[Company Name](https://company-website.com): one-sentence summary`
   - If a row has no website/URL value, bold the name instead: `**Company Name**: one-sentence summary`.

## Rules

- Condense each row's summary field down to exactly ONE sentence capturing its single most important fact — never copy a longer summary field verbatim.
- Sort sections in the order their funnel first appears among the given rows. Within each section, preserve the rows' original relative order exactly as given — do NOT sort alphabetically, by Category, or by anything else; a row that appears earlier in the input must appear earlier in its section.
- Output ONLY the funnel sub-headings and bullets described above — nothing else. No preamble, no closing remarks, no commentary, no code fences.
