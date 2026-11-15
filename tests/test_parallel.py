from dotenv import load_dotenv
from parallel import Parallel
import anthropic

load_dotenv()  # reads .env into os.environ
import time

client = Parallel()
claude = anthropic.Anthropic()


# Create FindAll Run
findall_run = client.beta.findall.create(
    objective="Find all sources for positron (AI Chip) headcount",
    entity_type="articles",
    match_conditions=[
  {
    "name": "headcount_data_positron_check",
    "description": "Article must provide headcount data for Positron (AI Chip)."
  },
  {
    "name": "trustworthy_source_check",
    "description": "Source must be from a reputable, verifiable publisher (e.g. established news outlets, official company disclosures, or recognized industry/business databases), not an anonymous blog, forum post, or unverified social media content."
  }
],
    generator="core",
    match_limit=10,
)

print(f"Created findall run with ID: {findall_run.findall_id}")

status = "queued"
terminal_statuses = ["completed", "failed", "cancelled"]

while status not in terminal_statuses:
    findall_run = client.beta.findall.retrieve(
        findall_id=findall_run.findall_id,
    )
    status = findall_run.status.status

    if status not in terminal_statuses:
        time.sleep(1)

# Get FindAll Run Result
run_result = client.beta.findall.result(
    findall_id=findall_run.findall_id,
)
result_json = run_result.model_dump_json(indent=2)
print(f"FindAll run {findall_run.findall_id} result: {result_json}")

# Have Claude digest the FindAll result into a readable summary
digest = claude.messages.create(
    model="claude-opus-4-8",
    max_tokens=4096,
    messages=[{
        "role": "user",
        "content": (
            "From the headcount data below, give a one-line summary showing the starting "
            "and ending headcount, the date range, and the percentage change. "
            "Format it exactly like this example: "
            "\"10 employees to 50 from 2020 to 2026, 500% increase.\"\n\n"
            + result_json
        ),
    }],
)
summary = next(block.text for block in digest.content if block.type == "text")
print(f"\nClaude digest of FindAll run {findall_run.findall_id}:\n{summary}")