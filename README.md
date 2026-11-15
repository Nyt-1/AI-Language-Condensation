# Sourcing Automation Pipeline

Turns Slack conversation into a running deal-flow table in Coda, with weekly digests posted back to Slack along the way. Rows can be pushed to a CRM (Attio) on request, and the whole table can be exported as a Word doc. Everything runs in a single configured timezone.

1. **Weekly (scheduled)** — `Pipeline_Digest.py` reads the pipeline Slack channel, drafts the digest, tags each entry with an owner, posts it to Slack, and writes straight to the Coda master table.
2. That success triggers `Raw_Digest.py`, which reads the raw-intel channel, drafts a "Company Intelligence Report," and posts it to Slack.
3. That success triggers `Push_to_CODA.py`, which parses that report into the same Coda table (merging in anything already known about a company) and DMs an added/updated list asking for assignments.
4. Whenever someone replies to that DM, run `Slack_Assignment.py` manually to read the reply and set the Assignee column in Coda.

Separately, on request:
- `Push_to_Attio.py` pushes any Coda row checked "Push to Attio" over to Attio.
- `Generate_Doc.py` turns the Coda table into `report.docx`, uploads it to Slack and provides a link to the document.

Pipeline_Digest.py and Push_to_CODA.py both write to the same Coda table, so a company can come from either channel.

| Workflow | File | Trigger |
|---|---|---|
| **1. Deal Pipeline Digest** | `Pipeline_Digest.py` / `pipeline_digest.yml` | Weekly on a schedule, or manually |
| **2. Raw Digest** | `Raw_Digest.py` / `raw_digest.yml` | Right after "1. Deal Pipeline Digest" succeeds, or manually |
| **3. Push Raw to CODA** | `Push_to_CODA.py` / `push_to_coda.yml` | Right after "2. Raw Digest" succeeds, or manually |
| **4. Assign Companies** | `Slack_Assignment.py` / `slack_assignment.yml` | Manually, or via an issue titled `4. Assign Companies` — it just checks the latest message in the notification channel, so run it after you've replied |
| **5. Push to Attio** | `Push_to_Attio.py` / `push_to_attio.yml` | Manually, or via an issue titled `5. Push to Attio` |
| **Generate Document Report** | `Generate_Doc.py` / `generate_doc.yml` | Manually, or via an issue titled `Generate Document` |
| **Publish skill version** | `Publish_Skill.py` / `publish_skill.yml` | Automatic on push to `main` when anything under `.claude/skills/` changes — only republishes the skill(s) that actually changed |

To trigger this workflow manually from Slack: type `\` + Enter in any channel and Slack autofills the slash command.

## One-time setup

1. `pip install -r requirements.txt`
2. Add the values below as GitHub Actions repository secrets (Settings → Secrets and variables → Actions). For local runs, copy `.env.example` to `.env` and fill it in — `.env` is gitignored.

**Credentials**

| Secret | Used by | How to get it |
|---|---|---|
| `SLACK_BOT_TOKEN` | Everything except `Publish_Skill.py` | Create a Slack app with scopes `channels:history`, `chat:write`, `channels:read`, `users:read`, `files:write`, install it to the workspace, grab the Bot User OAuth Token (`xoxb-…`) |
| `ANTHROPIC_API_KEY` | `Raw_Digest.py`, `Pipeline_Digest.py`, `Push_to_CODA.py`, `Publish_Skill.py` | console.anthropic.com → Settings → API Keys |
| `CODA_API_TOKEN` | `Push_to_CODA.py`, `Pipeline_Digest.py`, `Slack_Assignment.py`, `Push_to_Attio.py`, `Generate_Doc.py` | Coda → account icon → Settings → API settings → Generate API token |
| `ATTIO_API_KEY` | `Push_to_Attio.py` | Attio → Workspace Settings → Developers → API tokens |
| `PARALLEL_API_KEY` | `tests/test_parallel.py` (local only) | platform.parallel.ai dashboard |

**Skill IDs**

| Secret | Used by | How to get it |
|---|---|---|
| `COMPANY_SKILL_ID` | `Raw_Digest.py`, `Publish_Skill.py` | Run `python .github/scripts/Publish_Skill.py company-intelligence-report` once locally to create the skill and print the ID |
| `PHOTO_LINK_SKILL_ID` | `Raw_Digest.py`, `Pipeline_Digest.py`, `Publish_Skill.py` | `python .github/scripts/Publish_Skill.py photo-link-reader` |
| `PIPELINE_SKILL_ID` | `Pipeline_Digest.py`, `Publish_Skill.py` | `python .github/scripts/Publish_Skill.py deal-pipeline-digest` |
| `DOC_FORMAT_SKILL_ID` | `Publish_Skill.py` | `python .github/scripts/Publish_Skill.py generate-doc-format` |

**Slack channels** (channel IDs like `C0123ABCD`, from the channel's details pane in Slack)

| Secret | Used by | What it is |
|---|---|---|
| `RAW_SOURCE_CHANNEL` | `Raw_Digest.py` | the raw-intel channel |
| `PIPELINE_SOURCE_CHANNEL` | `Pipeline_Digest.py` | the deal-pipeline channel |
| `DEST_CHANNEL_ID` | `Raw_Digest.py`, `Push_to_CODA.py`, `Pipeline_Digest.py` | where digests get posted |
| `NOTIFICATION_CHANNEL` | `Push_to_CODA.py`, `Slack_Assignment.py` | where assignment requests/replies happen |
| `REPORT_DEST_CHANNEL` | `Generate_Doc.py` | where generated docs get dropped |

**Coda** (find table IDs with `curl -H "Authorization: Bearer YOUR_TOKEN" https://coda.io/apis/v1/docs/CODA_DOC_ID/tables`)

| Secret | Used by | What it is |
|---|---|---|
| `CODA_DOC_ID` | all Coda scripts | From your Coda doc's URL: `coda.io/d/Doc-Name_dCODA_DOC_ID` — the ID is everything after `_d` |
| `CODA_TABLE_ID` | all Coda scripts | the master table |
| `CODA_TIMELINE_ID` | `Push_to_CODA.py`, `Pipeline_Digest.py` | the weekly timeline table (exposed to scripts as `CODA_TIMELINE_TABLE_ID`) |
| `CODA_REPORTS_TABLE_ID` | `Generate_Doc.py` | where generated-report links are logged |

**Team**

| Secret | Used by | What it is |
|---|---|---|
| `TEAM_MEMBERS` | `Pipeline_Digest.py`, `Push_to_CODA.py`, `Slack_Assignment.py`, `Push_to_Attio.py` | JSON mapping each team member's display name to their work email, e.g. `{"Alice": "alice@example.com", "Bob": "bob@example.com"}`. The names are the valid owners/assignees; the emails set the Attio deal owner (use `""` if someone has no Attio seat). The first name is @-mentioned on the assignment prompt. |

`Push_to_Attio.py` and `Slack_Assignment.py` import shared code from `Push_to_CODA.py`, which reads `SLACK_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `DEST_CHANNEL_ID` and the Coda IDs at import time — so those must be set for them too.

**Hardcoded in the source:** the timezone (`TZ` in `Raw_Digest.py` / `Pipeline_Digest.py`), the Claude model (`ANTHROPIC_MODEL` in `Push_to_CODA.py`), Deal Funnel stage names, and the Attio object/attribute slugs (`Push_to_Attio.py`).

## Assigning companies in Slack

After `Push_to_CODA.py` runs, it posts a numbered added/updated list to the notification channel. Reply there with:

- `Assign 1-10 to Alice` — added companies #1–10
- `Reassign 4-6 to Bob` — updated companies #4-6
- `Assign 1-10 to Alice; Assign 11-20 to Carol; Reassign 1-6 to Bob`

Names must match a key in `TEAM_MEMBERS`.

Not valid: `Assign 1-10 to Alice, 11-20 to Carol`

## Using the Coda report

Coda holds a master table (one row per company, latest state) and a timeline table (what was reported each week, logged before any merging so it's untouched by later edits). Be careful with "equals" vs "contains" filters when searching.

Returning companies carry their Category/Assignee/Deal Funnel forward, but not quite the same way in both scripts:

- `Push_to_CODA.py` always carries Category/Assignee forward.
- `Pipeline_Digest.py` only carries Category/Assignee/Deal Funnel forward if the company's already at a later funnel stage — anything at an early stage (or blank) resets all three like a fresh entry. The summary itself always merges either way.

Product Area always carries forward in both scripts, regardless of funnel stage. For a brand-new company, it's auto-filled by asking Claude for a short label based on the company's summary (`classify_product_area` in `Push_to_CODA.py`), instead of being left blank like Category.

## Exporting to Attio

`Push_to_Attio.py` pushes any Coda row with "Push to Attio" checked (latest row per company). Matches existing Attio records by company name instead of creating duplicates.

## Updating the report skills

Three skills are in play:

- `company-intelligence-report` — the raw-intel digest format (`Raw_Digest.py`)
- `photo-link-reader` — reads facts from shared images/links, used by both digests
- `deal-pipeline-digest` — the deal-pipeline digest format + owner tagging (`Pipeline_Digest.py`)

Edit the skill's folder under `.claude/skills/` and push to `main` — `publish_skill.yml` republishes whichever skill(s) changed.

There's a fourth one, `generate-doc-format`, still registered and still auto-published, but `Generate_Doc.py` doesn't call it anymore. Left in place in case doc formatting goes back to AI-driven later.

## Generating a document report

`Generate_Doc.py` pulls every row from Coda and formats it — plain Python, no Claude involved — into `output/report.docx`:

1. **Current Pipeline** — latest row per company, grouped by Deal Funnel, one linked bullet each, tagged with its Product Area when set.
2. **Weekly history** — every week ever logged, most recent first, one page each, still deduped to one entry per company per week.

Each bullet uses the Coda `Summary` field as-is. The only cleanup is collapsing embedded newlines/extra whitespace to single spaces — otherwise a messy row can break the bullet-list formatting for everything after it.

- **Trigger**: an issue titled `Generate Document`, or run the workflow manually.
- **Getting the file**: the workflow uploads `report.docx` straight to Slack and logs the permalink. Only people in that Slack channel (or the org, if public link sharing is on) can open it.
- **Changing the format**: edit `_format_rows_as_markdown()` in `Generate_Doc.py` directly — no separate skill file anymore.

Every run logs a row with the current date/time and a link to that run's Slack upload, written only after the upload actually succeeds.
