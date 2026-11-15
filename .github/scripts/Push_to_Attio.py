import os
import time
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv

load_dotenv()

from Push_to_CODA import CODA_TOKEN, CODA_DOC_ID, CODA_TABLE_ID, TEAM_MEMBERS, coda_request

ATTIO_API_KEY = os.environ["ATTIO_API_KEY"]

ATTIO_COMPANIES_OBJECT = "companies"
ATTIO_COMPANY_MATCH_ATTRIBUTE = "name"

ATTIO_DEALS_OBJECT = "deals"
ATTIO_DEAL_COMPANY_RELATIONSHIP = "associated_company"

DEFAULT_DEAL_STAGE = "Fund"
OWNER_EMAILS = TEAM_MEMBERS


COMPANY_FIELD_MAP = {
    "summary": ("description", "text", "Description"),
    "website": ("domains", "domain", "Domain"),
}

DEAL_FIELD_MAP = {
    "deal_funnel": ("internal_status", "select", "Deal Funnel"),
    "category": ("hit_list", "select", "Category"),
    "deal_owner": ("owner", "actor", "Deal Owner"),
    "investors": ("investors_1", "text", "Investors"),
    "platform_support": ("platform_support", "checkbox", "Platform Support"),
    "customers": ("customers", "text", "Customers"),
    "icp": ("icp", "text", "ICP"),
}


def _attio_value(attio_type: str, value):
    if attio_type == "select":
        return {"option": value.replace("—", "–")}
    if attio_type == "domain":
        return value
    if attio_type == "status":
        return {"status": value}
    if attio_type == "actor":
        return {"workspace_member_email_address": value}
    return {"value": value}


def _changed_fields(item: dict, field_map: dict) -> tuple[dict, dict]:
    """Build the Attio values, skipping fields left blank in Coda."""
    values = {}
    changed = {}
    for coda_key, (slug, attio_type, label) in field_map.items():
        value = item.get(coda_key)
        has_value = value is not None if attio_type == "checkbox" else bool(value)
        if has_value:
            values[slug] = [_attio_value(attio_type, value)]
            changed[label] = value
    return values, changed


def fetch_checked_rows() -> list[dict]:
    """
    Pull rows from the main Coda companies table where both "Push to Attio"
    and "Latest?" are checked.
    """
    url = f"https://coda.io/apis/v1/docs/{CODA_DOC_ID}/tables/{CODA_TABLE_ID}/rows"
    headers = {"Authorization": f"Bearer {CODA_TOKEN}"}
    matched = []
    page_token = None
    while True:
        params = {"limit": 500, "valueFormat": "simple", "useColumnNames": "true"}
        if page_token:
            params["pageToken"] = page_token
        response = coda_request("GET", url, headers, params=params)
        if response is None or not response.ok:
            status = getattr(response, "status_code", "no response")
            body = getattr(response, "text", "")
            raise Exception(f"Coda fetch error: {status} — {body}")
        data = response.json()
        for row in data.get("items", []):
            cells = row.get("values", {})
            name = cells.get("Company", "").strip()
            if not name:
                continue
            if not (bool(cells.get("Push to Attio", False)) and bool(cells.get("Latest?", False))):
                continue
            matched.append({
                "name": name,
                "summary": cells.get("Summary", "").strip(),
                "website": cells.get("Website", "").strip(),
                "deal_funnel": cells.get("Deal Funnel", "").strip(),
                "category": cells.get("Category", "").strip(),
                "deal_owner": OWNER_EMAILS.get(cells.get("Deal Owner", "").strip(), ""),
                "investors": cells.get("Investors", "").strip(),
                "platform_support": bool(cells.get("Platform Support", False)),
                "customers": cells.get("Customers", "").strip(),
                "icp": cells.get("ICP", "").strip(),
            })
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return matched


def attio_request(method: str, url: str, max_retries: int = 5, **kwargs):
    headers = {
        "Authorization": f"Bearer {ATTIO_API_KEY}",
        "Content-Type": "application/json",
    }
    response = None
    for attempt in range(max_retries):
        try:
            response = requests.request(method, url, headers=headers, **kwargs)
        except requests.RequestException as e:
            wait = 2 ** attempt
            print(f"Attio request failed ({e}); attempt {attempt + 1}/{max_retries}, waiting {wait}s...")
            time.sleep(wait)
            continue
        if response.status_code != 429 and response.status_code < 500:
            return response
        retry_after = response.headers.get("Retry-After")
        wait = float(retry_after) if retry_after else (2 ** attempt)
        print(f"Attio {response.status_code} (attempt {attempt + 1}/{max_retries}); waiting {wait}s...")
        time.sleep(wait)
    return response


def find_company_by_name(name: str) -> str | None:
    """`name` isn't marked as a unique attribute in this workspace, so it can't be used
    with Attio's assert/upsert endpoint — look up an existing Company by that value
    instead, the same way Deals are looked up by their linked Company."""
    url = f"https://api.attio.com/v2/objects/{ATTIO_COMPANIES_OBJECT}/records/query"
    response = attio_request(
        "POST",
        url,
        json={"filter": {ATTIO_COMPANY_MATCH_ATTRIBUTE: name}, "limit": 1},
    )
    if response is None or not response.ok:
        status = getattr(response, "status_code", "no response")
        body = getattr(response, "text", "")
        raise Exception(f"Attio company lookup error: {status} — {body}")

    results = response.json().get("data", [])
    return results[0]["id"]["record_id"] if results else None


def push_company_record(item: dict) -> tuple[str, dict]:
    """Find (or create) the Company record for this name. Returns its record_id and
    whatever fields were actually pushed (for the Notes log)."""
    values, changed = _changed_fields(item, COMPANY_FIELD_MAP)

    company_record_id = find_company_by_name(item["name"])
    if company_record_id:
        url = f"https://api.attio.com/v2/objects/{ATTIO_COMPANIES_OBJECT}/records/{company_record_id}"
        response = attio_request("PATCH", url, json={"data": {"values": values}})
    else:
        values[ATTIO_COMPANY_MATCH_ATTRIBUTE] = [_attio_value("text", item["name"])]
        url = f"https://api.attio.com/v2/objects/{ATTIO_COMPANIES_OBJECT}/records"
        response = attio_request("POST", url, json={"data": {"values": values}})

    if response is None or not response.ok:
        status = getattr(response, "status_code", "no response")
        body = getattr(response, "text", "")
        raise Exception(f"Attio company error for {item['name']!r}: {status} — {body}")

    company_record_id = company_record_id or response.json()["data"]["id"]["record_id"]
    if changed:
        add_change_note(ATTIO_COMPANIES_OBJECT, company_record_id, changed)
    return company_record_id, changed


def find_deal_by_company(company_record_id: str) -> str | None:
    """Look for an existing Deal linked to this Company instead."""
    url = f"https://api.attio.com/v2/objects/{ATTIO_DEALS_OBJECT}/records/query"
    response = attio_request(
        "POST",
        url,
        json={
            "filter": {ATTIO_DEAL_COMPANY_RELATIONSHIP: {"target_record_id": company_record_id}},
            "limit": 1,
        },
    )
    if response is None or not response.ok:
        status = getattr(response, "status_code", "no response")
        body = getattr(response, "text", "")
        raise Exception(f"Attio deal lookup error: {status} — {body}")

    results = response.json().get("data", [])
    return results[0]["id"]["record_id"] if results else None


def push_deal_record(item: dict, company_record_id: str, company_changed: dict) -> tuple[str, dict]:
    """Update the Deal linked to this Company, creating one (and linking it) if none
    exists yet."""
    values, changed = _changed_fields(item, DEAL_FIELD_MAP)
    values[ATTIO_DEAL_COMPANY_RELATIONSHIP] = [
        {"target_object": ATTIO_COMPANIES_OBJECT, "target_record_id": company_record_id}
    ]

    deal_record_id = find_deal_by_company(company_record_id)
    if deal_record_id:
        url = f"https://api.attio.com/v2/objects/{ATTIO_DEALS_OBJECT}/records/{deal_record_id}"
        response = attio_request("PATCH", url, json={"data": {"values": values}})
    else:
        values["name"] = [{"value": item["name"]}]
        values["stage"] = [_attio_value("status", DEFAULT_DEAL_STAGE)]
        url = f"https://api.attio.com/v2/objects/{ATTIO_DEALS_OBJECT}/records"
        response = attio_request("POST", url, json={"data": {"values": values}})

    if response is None or not response.ok:
        status = getattr(response, "status_code", "no response")
        body = getattr(response, "text", "")
        raise Exception(f"Attio deal error for {item['name']!r}: {status} — {body}")

    deal_record_id = deal_record_id or response.json()["data"]["id"]["record_id"]
    note_changed = {**company_changed, **changed}
    if note_changed:
        add_change_note(ATTIO_DEALS_OBJECT, deal_record_id, note_changed)
    return deal_record_id, changed


def add_change_note(parent_object: str, record_id: str, changed: dict) -> None:
    """Create a new Note listing the fields pushed this run."""
    content = "\n".join(f"{label}: {value}" for label, value in changed.items())
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    url = "https://api.attio.com/v2/notes"
    response = attio_request(
        "POST",
        url,
        json={
            "data": {
                "parent_object": parent_object,
                "parent_record_id": record_id,
                "title": f"Pipeline Update — {timestamp}",
                "format": "plaintext",
                "content": content,
            }
        },
    )
    if response is None or not response.ok:
        status = getattr(response, "status_code", "no response")
        body = getattr(response, "text", "")
        raise Exception(f"Attio note error for record {record_id!r}: {status} — {body}")


def push_company(item: dict) -> str:
    company_record_id, company_changed = push_company_record(item)
    push_deal_record(item, company_record_id, company_changed)
    return "upserted"


def run():
    print("Fetching companies checked 'Push to Attio' from Coda...")
    rows = fetch_checked_rows()
    print(f"Found {len(rows)} row(s) to push.")

    succeeded, failed = [], []
    for item in rows:
        try:
            outcome = push_company(item)
            print(f"  {item['name']}: {outcome}")
            succeeded.append(item["name"])
        except Exception as e:
            print(f"  {item['name']}: FAILED — {e}")
            failed.append(item["name"])

    print(f"\nDone. {len(succeeded)} succeeded, {len(failed)} failed.")
    if failed:
        print(f"Failed: {failed}")


if __name__ == "__main__":
    run()
