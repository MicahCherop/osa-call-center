"""One-time import of the existing Google Sheets call-center data into Supabase."""
import base64
import json
import os
from collections import defaultdict
from typing import Any, Dict, Iterable, List

import gspread
from google.oauth2.service_account import Credentials
from supabase import create_client

SCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
CATEGORY_SHEETS = {"Defaulted", "Defaulters", "Upcoming Dues", "Active No Loan", "Active With No Loans", "Active No Loans", "Dormant"}


def value(record: Dict[str, Any], *names: str) -> str:
    normalized = {"".join(char.lower() for char in key if char.isalnum()): item for key, item in record.items()}
    for name in names:
        item = normalized.get("".join(char.lower() for char in name if char.isalnum()))
        if item not in (None, ""):
            return str(item).strip()
    return ""


def number(item: str):
    try:
        return float(item.replace(",", "")) if item else None
    except ValueError:
        return None


def client():
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"].strip()
    credentials = json.loads(raw) if raw.startswith("{") else json.loads(base64.b64decode(raw).decode("utf-8"))
    sheets = gspread.authorize(Credentials.from_service_account_info(credentials, scopes=SCOPE)).open_by_key(os.environ["GOOGLE_SHEET_ID"])
    return sheets, create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def batches(rows: List[Dict[str, Any]], size: int = 500) -> Iterable[List[Dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def main():
    sheets, database = client()
    agents_sheet = sheets.worksheet("Agents")
    agents = [{"name": value(row, "Name"), "email": value(row, "Email").lower(), "role": value(row, "Role") or "Control Agent", "status": value(row, "Status") or "Active"} for row in agents_sheet.get_all_records() if value(row, "Email")]
    for batch in batches(agents):
        database.table("agents").upsert(batch, on_conflict="email").execute()

    registry = sheets.worksheet("Campaigns").get_all_records()
    campaigns = [{"name": value(row, "name", "campaign"), "type": value(row, "type", "campaign type"), "priority": value(row, "priority"), "start_date": value(row, "start date") or None, "end_date": value(row, "end date") or None, "date_added": value(row, "date added") or None} for row in registry if value(row, "name", "campaign")]
    for batch in batches(campaigns):
        database.table("campaigns").upsert(batch, on_conflict="name").execute()

    campaign_rows = database.table("campaigns").select("id,name,type").execute().data
    by_type = defaultdict(list)
    for campaign in campaign_rows:
        by_type[campaign["type"].strip().lower()].append(campaign)
    imported = 0
    for worksheet in sheets.worksheets():
        if worksheet.title not in CATEGORY_SHEETS:
            continue
        candidates = by_type.get(worksheet.title.strip().lower(), [])
        if not candidates:
            continue
        for row in worksheet.get_all_records():
            customer_id = value(row, "id", "customer id")
            if not customer_id:
                continue
            campaign_name = value(row, "campaign")
            campaign = next((item for item in candidates if item["name"] == campaign_name), candidates[0])
            customer = {"campaign_id": campaign["id"], "customer_id": customer_id, "name": value(row, "name", "customer name"), "phone": value(row, "phone", "mobile no"), "branch": value(row, "branch", "station", "stations"), "sector": value(row, "sector"), "balance": number(value(row, "balance")), "due_date": value(row, "due date") or None, "pair": value(row, "pair"), "disb_amount": number(value(row, "disb amount")), "total_paid": number(value(row, "total paid")), "worked": value(row, "worked", "is worked").upper() == "TRUE", "outcome": value(row, "outcome"), "status": value(row, "status", "account status"), "business_status": value(row, "business status"), "ptp_amount": number(value(row, "ptp amount")), "ptp_time": value(row, "ptp time"), "feedback": value(row, "feedback")}
            database.table("customers").upsert(customer, on_conflict="campaign_id,customer_id").execute()
            imported += 1
    print(f"Imported {len(agents)} agents, {len(campaigns)} campaigns, and {imported} customers.")


if __name__ == "__main__":
    main()