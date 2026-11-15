---
name: photo-link-reader
description: Extract facts from images (screenshots, photos, pitch-deck slides) and linked pages included in a message transcript, so they can be used alongside the transcript's text as source material. Use this skill whenever the input includes shared images or embedded links that may carry facts relevant to the task.
---

# Photo & Link Reader

Images and links included in a transcript are transcript content, not
external research. This skill defines how to read them and where the line
against "external research" still applies — it doesn't change or override
any other skill's own rules about facts; it only tells you how to read the
non-text content a transcript contains.

## Workflow

1. **Images are read like any other transcript content.** If the transcript
   includes an image (a screenshot, a photo of a slide, a business card),
   read whatever facts are visibly stated in it and treat them as if they were a quote. Never invent or infer details the image doesn't actually show, and never guess at text that's too blurry or cropped to read.
2. **Links get fetched, not just noted.** If a message contains a URL, use
   the `web_fetch` tool to read that specific page and pull facts from it.
   This is reading a source the transcript pointed you to — not general web
   research.
3. **Don't research outward.** Only fetch URLs that appear directly in the
   transcript. Never follow a link found *inside* a fetched page to keep
   researching further — that crosses back into external research.
4. **`web_search` is a separate tool with a separate job.** Nothing here
   changes when a task's own instructions call for `web_search` (e.g. to
   verify a company is real or find its official website) — that boundary is
   set by the calling task, not by this skill.

## Output

This skill doesn't define its own output format — it feeds facts into
whatever task invoked it (e.g. a report-writing skill). Report facts from
images and linked pages the same way you'd report a fact typed directly into
a message: plainly, without noting "per the image" or "per the linked page"
unless the calling task's own format asks for source attribution.
