import os
import json
import base64
import threading
import time
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="OSA Call Center API")

_sheet_cache = None
_sheet_cache_lock = threading.Lock()
_category_sheet_lock = threading.Lock()
_campaign_registry_cache = None
_campaign_sheet_cache = {}
_agents_data_cache = None
_agents_data_cache_time = 0
_agents_data_cache_lock = threading.Lock()
_customer_queue_cache = {}
_customer_queue_cache_lock = threading.Lock()

# Enable CORS for Vercel Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


def get_google_sheet():
    global _sheet_cache

    if _sheet_cache is not None:
        return _sheet_cache

    try:
        creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        if not creds_json:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not configured")

        if creds_json.startswith('"') and creds_json.endswith('"'):
            creds_json = json.loads(creds_json)
        if creds_json.startswith("{") and creds_json.endswith("}"):
            creds_dict = json.loads(creds_json)
        else:
            creds_dict = json.loads(base64.b64decode(creds_json).decode("utf-8"))
        if not creds_dict.get("client_email") or not creds_dict.get("private_key"):
            raise ValueError("service account JSON is missing client_email or private_key")

        sheet_id = os.getenv("GOOGLE_SHEET_ID", "1VmLCg_6iY0QsjPbDjgDRNugFyyNRWABtPIASGDepZeU").strip()
        if not sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is not configured")

        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
        client = gspread.authorize(creds)
        with _sheet_cache_lock:
            if _sheet_cache is None:
                _sheet_cache = client.open_by_key(sheet_id)
            return _sheet_cache
    except HTTPException:
        raise
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=503, detail=f"Google credentials are not valid JSON: {error.msg}")
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"Google Sheets connection failed: {error}")


def normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Expose stable keys regardless of worksheet header spelling or casing."""
    normalized = {}
    for key, value in record.items():
        if key:
            compact_key = "".join(char.lower() for char in str(key) if char.isalnum())
            aliases = {
                "customerid": "id", "customername": "name", "phonenumber": "phone",
                "campaignname": "campaign", "assignedagent": "agentId", "agent": "agentId",
                "agentid": "agentId", "isworked": "worked", "accountstatus": "status",
                "campaignpriority": "priority", "startdate": "startDate", "enddate": "endDate",
                "callsmade": "callsMade", "calls": "callsMade", "connectedcalls": "connected",
                "amountrecovered": "conversion", "recovered": "conversion",
                "daysinactive": "daysInactive", "daysdormant": "daysDormant",
                "lastloanamount": "lastLoanAmount", "loyalty": "loyalty", "feedback": "feedback",
            }
            if compact_key in aliases:
                normalized[aliases[compact_key]] = value
                continue

            words = "".join(char if char.isalnum() else " " for char in str(key)).split()
            camel_key = words[0].lower() + "".join(word.title() for word in words[1:])
            normalized[camel_key] = value
    if not normalized.get("branch"):
        normalized["branch"] = normalized.get("station") or normalized.get("stations", "")
    normalized.pop("station", None)
    normalized.pop("stations", None)
    return normalized


def records_from_rows(rows: List[List[Any]], headers: List[str]) -> List[Dict[str, Any]]:
    return [
        normalize_record(dict(zip(headers, row + [""] * (len(headers) - len(row)))))
        for row in rows
        if any(str(value).strip() for value in row)
    ]


def worksheet_records(worksheet) -> List[Dict[str, Any]]:
    last_error = None
    for attempt in range(2):
        try:
            values = worksheet.get_all_values()
            break
        except Exception as error:
            last_error = error
            if attempt == 0:
                time.sleep(0.25)
    else:
        raise last_error
    if not values:
        return []
    headers = values[0]
    return records_from_rows(values[1:], headers)


def column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


CUSTOMER_HEADERS = [
    "id", "name", "phone", "branch", "sector", "balance", "campaign",
    "agentId", "worked", "outcome", "status", "dueDate",
    "pair", "disbAmount", "totalPaid", "businessStatus", "ptpAmount", "ptpTime"
]

CATEGORY_HEADERS = {
    "Defaulted": ["Name", "Mobile No", "Due Date", "Branch", "Pair", "Sector", "Disb Amount", "Total Paid", "Balance", "Campaign"],
    "Upcoming Dues": ["Name", "Mobile No", "Due Date", "Branch", "Pair", "Sector", "Disb Amount", "Total Paid", "Balance", "Campaign"],
    "Active No Loan": ["Name", "Mobile No", "Due Date", "Branch", "Pair", "Sector", "Disb Amount", "Campaign"],
    "Dormant": ["Name", "Mobile No", "Due Date", "Branch", "Pair", "Sector", "Disb Amount", "Campaign"],
}

CATEGORY_SHEET_ALIASES = {
    "Defaulted": ("Defaulted", "Defaulters"),
    "Upcoming Dues": ("Upcoming Dues",),
    "Active No Loan": ("Active No Loan", "Active With No Loans", "Active No Loans"),
    "Dormant": ("Dormant",),
}


def category_sheet_name(category: str) -> str:
    """Convert an upload category into a safe, stable worksheet title."""
    cleaned = " ".join(str(category or "Uncategorized").strip().split())
    for source in ("_", "-"):
        cleaned = cleaned.replace(source, " ")
    return " ".join(cleaned.split()).title()[:100] or "Uncategorized"


def get_or_create_category_sheet(spreadsheet, category: str):
    title = category_sheet_name(category)
    headers = CATEGORY_HEADERS.get(title, CUSTOMER_HEADERS)
    try:
        worksheet = spreadsheet.worksheet(title)
        if worksheet.col_count < len(headers):
            worksheet.resize(cols=len(headers))
        current_headers = worksheet.row_values(1)
        missing = [header for header in headers if header not in current_headers]
        for index, header in enumerate(missing, start=len(current_headers) + 1):
            worksheet.update_cell(1, index, header)
        return worksheet
    except gspread.exceptions.WorksheetNotFound:
        with _category_sheet_lock:
            try:
                worksheet = spreadsheet.worksheet(title)
                if worksheet.col_count < len(headers):
                    worksheet.resize(cols=len(headers))
                current_headers = worksheet.row_values(1)
                missing = [header for header in headers if header not in current_headers]
                for index, header in enumerate(missing, start=len(current_headers) + 1):
                    worksheet.update_cell(1, index, header)
                return worksheet
            except gspread.exceptions.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(
                    title=title,
                    rows=1000,
                    cols=len(headers),
                )
                worksheet.append_row(headers)
                return worksheet


def campaign_sheet_name(campaign: str) -> str:
    cleaned = " ".join(str(campaign or "Campaign").strip().split())
    for character in ("/", "\\", "?", "*", "[", "]", ":"):
        cleaned = cleaned.replace(character, " ")
    return " ".join(cleaned.split())[:100] or "Campaign"


def get_or_create_campaign_sheet(spreadsheet, campaign: str):
    global _campaign_sheet_cache
    title = campaign_sheet_name(campaign)
    if title in _campaign_sheet_cache:
        return _campaign_sheet_cache[title]
    try:
        worksheet = spreadsheet.worksheet(title)
        if worksheet.col_count < len(CUSTOMER_HEADERS):
            worksheet.resize(cols=len(CUSTOMER_HEADERS))
        if worksheet.row_values(1) != CUSTOMER_HEADERS:
            worksheet.update("A1", [CUSTOMER_HEADERS])
        _campaign_sheet_cache[title] = worksheet
        return worksheet
    except gspread.exceptions.WorksheetNotFound:
        with _category_sheet_lock:
            try:
                worksheet = spreadsheet.worksheet(title)
                if worksheet.col_count < len(CUSTOMER_HEADERS):
                    worksheet.resize(cols=len(CUSTOMER_HEADERS))
                if worksheet.row_values(1) != CUSTOMER_HEADERS:
                    worksheet.update("A1", [CUSTOMER_HEADERS])
                _campaign_sheet_cache[title] = worksheet
                return worksheet
            except gspread.exceptions.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(CUSTOMER_HEADERS))
                worksheet.append_row(CUSTOMER_HEADERS)
                _campaign_sheet_cache[title] = worksheet
                return worksheet


def get_or_create_campaign_registry(spreadsheet):
    global _campaign_registry_cache
    headers = ["name", "type", "priority", "startDate", "endDate", "dateAdded"]
    if _campaign_registry_cache is not None:
        return _campaign_registry_cache
    try:
        worksheet = spreadsheet.worksheet("Campaigns")
        if not worksheet.row_values(1):
            worksheet.update("A1", [headers])
        _campaign_registry_cache = worksheet
        return worksheet
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title="Campaigns", rows=100, cols=len(headers))
        worksheet.append_row(headers)
        _campaign_registry_cache = worksheet
        return worksheet


def ensure_customer_headers(worksheet) -> None:
    if worksheet.col_count < len(CUSTOMER_HEADERS):
        worksheet.resize(cols=len(CUSTOMER_HEADERS))
    headers = worksheet.row_values(1)
    missing = [header for header in CUSTOMER_HEADERS if header not in headers]
    for index, header in enumerate(missing, start=len(headers) + 1):
        worksheet.update_cell(1, index, header)

# --- PYDANTIC SCHEMAS ---

class UserCreateModel(BaseModel):
    name: str
    email: str
    role: str
    status: str = "Active"
    # No password required!

class UserEditModel(BaseModel):
    email: str
    name: str
    role: str

class UserDeleteModel(BaseModel):
    email: str

class LoginModel(BaseModel):
    email: str

class DispositionModel(BaseModel):
    customerId: str
    outcome: str
    status: str
    amountRec: float = 0.0
    agentName: str
    comments: str = ""
    businessStatus: str = ""
    ptpTime: str = ""


class CustomerAssignModel(BaseModel):
    customerId: str
    agentName: str
    requesterRole: str = ""


class DistributionModel(BaseModel):
    campaign: str
    selectedAgents: List[str] = []
    requesterRole: str = ""

class CustomerUploadModel(BaseModel):
    id: int
    name: str
    phone: str
    branch: str
    sector: str
    balance: str
    campaign: str
    dueDate: str = ""
    station: str = ""
    stations: str = ""
    pair: str = ""
    disbAmount: str = ""
    totalPaid: str = ""

    class Config:
        extra = "allow"


def can_allocate(requester_role: str) -> bool:
    return requester_role.strip().lower() in {"admin", "ops manager", "team leader"}


def category_row(customer: CustomerUploadModel, category: str) -> List[Any]:
    values = {
        "Name": customer.name,
        "Mobile No": customer.phone,
        "Due Date": customer.dueDate,
        "Branch": customer.branch,
        "Pair": customer.pair,
        "Sector": customer.sector,
        "Disb Amount": customer.disbAmount,
        "Total Paid": customer.totalPaid,
        "Balance": customer.balance,
        "Campaign": customer.campaign,
    }
    headers = CATEGORY_HEADERS.get(category_sheet_name(category), CUSTOMER_HEADERS)
    return [values.get(header, "") for header in headers]


# --- ROOT & HEALTH ENDPOINTS ---
@app.get("/")
@app.get("/api")
def read_root():
    return {"message": "FastAPI Server is running successfully on Vercel!"}

@app.get("/health")
@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "message": "Call Center API is running",
        "googleCredentialsConfigured": bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()),
        "googleSheetConfigured": bool(os.getenv("GOOGLE_SHEET_ID", "").strip()),
    }


# --- AUTHENTICATION ---
@app.post("/login")
@app.post("/api/login")
def login(creds: LoginModel):
    try:
        email_lower = creds.email.lower().strip()
        
        # 1. DOMAIN FIREWALL: Require company email domain
        if not email_lower.endswith("@4g-capital.com"):
            raise HTTPException(status_code=403, detail="Unauthorized network. Only corporate @4g-capital.com accounts are permitted.")
            
        sheet = get_google_sheet().worksheet("Agents")
        agents = sheet.get_all_records()
        
        # 2. DATABASE CHECK: Does this exact email exist in the sheet?
        user = next((a for a in agents if str(a.get("Email", a.get("email", ""))).lower().strip() == email_lower), None)
        
        # 3. STRICT BLOCK: Not in the database
        if not user:
            raise HTTPException(status_code=403, detail="ACCESS DENIED: Your email is not registered in the active users database. Contact your Ops Manager.")
            
        # 4. ACTIVE STATUS CHECK: Ensure they haven't been deactivated
        status = str(user.get("Status", user.get("status", "Active"))).strip().lower()
        if status == "inactive":
             raise HTTPException(status_code=403, detail="ACCOUNT SUSPENDED: Your access to the system has been revoked.")

        role = user.get("Role", user.get("role", "Control Agent"))
        name = user.get("Name", user.get("name", "Unknown"))

        return {
            "success": True,
            "email": creds.email,
            "role": role,
            "name": name
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Database Login Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Secure database connection failed. Please try again later.")


# --- USER MANAGEMENT (ADMIN ENDPOINTS) ---

@app.get("/agents")
@app.get("/api/agents")
def get_agents():
    global _agents_data_cache, _agents_data_cache_time
    cache_ttl = 30
    with _agents_data_cache_lock:
        if _agents_data_cache is not None and time.time() - _agents_data_cache_time < cache_ttl:
            return _agents_data_cache
    try:
        sheet = get_google_sheet().worksheet("Agents")
        records = worksheet_records(sheet)
        for record in records:
            record.pop("password", None)
        with _agents_data_cache_lock:
            _agents_data_cache = records
            _agents_data_cache_time = time.time()
        return records
    except HTTPException:
        with _agents_data_cache_lock:
            if _agents_data_cache is not None:
                return _agents_data_cache
        raise
    except Exception as error:
        with _agents_data_cache_lock:
            if _agents_data_cache is not None:
                return _agents_data_cache
        raise HTTPException(status_code=503, detail=f"Google Sheets connection failed: {error}")

@app.post("/api/users/add")
def add_user(user: UserCreateModel):
    try:
        sheet = get_google_sheet().worksheet("Agents")
        
        # Check if user already exists
        existing_users = sheet.get_all_records()
        for u in existing_users:
            if str(u.get("Email", "")).lower().strip() == user.email.lower().strip():
                raise HTTPException(status_code=400, detail="A user with this email already exists.")
        
        # Get headers to ensure we map correctly, or just append standard columns
        # Structure assuming: Status | Name | Email | Role
        row_data = [user.status, user.name, user.email, user.role]
        sheet.append_row(row_data)
        
        return {"status": "success", "message": f"User {user.name} created successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/users/edit")
def edit_user(user: UserEditModel):
    try:
        sheet = get_google_sheet().worksheet("Agents")
        records = sheet.get_all_records()
        
        row_idx = None
        for idx, record in enumerate(records):
            if str(record.get("Email", record.get("email", ""))).lower().strip() == user.email.lower().strip():
                row_idx = idx + 2  # +2 because header is row 1, and index starts at 0
                break
                
        if not row_idx:
            raise HTTPException(status_code=404, detail="User not found in database.")
            
        # Dynamically find column numbers based on headers
        headers = sheet.row_values(1)
        name_col = headers.index("Name") + 1 if "Name" in headers else 2
        role_col = headers.index("Role") + 1 if "Role" in headers else 4
        
        # Update specific cells
        sheet.update_cell(row_idx, name_col, user.name)
        sheet.update_cell(row_idx, role_col, user.role)
        
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/users/delete")
def delete_user(user: UserDeleteModel):
    try:
        sheet = get_google_sheet().worksheet("Agents")
        records = sheet.get_all_records()
        
        row_idx = None
        for idx, record in enumerate(records):
            if str(record.get("Email", record.get("email", ""))).lower().strip() == user.email.lower().strip():
                row_idx = idx + 2
                break
                
        if not row_idx:
            raise HTTPException(status_code=404, detail="User not found in database.")
            
        sheet.delete_rows(row_idx)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/agents/status")
@app.put("/api/agents/status")
def update_agent_status(name: str = Body(...), status: str = Body(...)):
    sheet = get_google_sheet().worksheet("Agents")
    try:
        cell = sheet.find(name)
        if not cell:
            raise HTTPException(status_code=404, detail="Agent not found")
        headers = sheet.row_values(1)
        status_column = next((index + 1 for index, header in enumerate(headers) if str(header).strip().lower() in {"status", "agentstatus", "shiftstatus"}), 1)
        sheet.update_cell(cell.row, status_column, status)
        return {"status": "success"}
    except gspread.exceptions.CellNotFound:
        raise HTTPException(status_code=404, detail="Agent not found in database")


# --- CAMPAIGNS & CUSTOMERS ---
@app.get("/campaigns")
@app.get("/api/campaigns")
def get_campaigns():
    spreadsheet = get_google_sheet()
    campaign_records = []
    try:
        sheet = spreadsheet.worksheet("Campaigns")
        campaign_records = [
            normalize_record(record)
            for record in worksheet_records(sheet)
            if str(record.get("name", record.get("campaign", ""))).strip()
        ]
    except gspread.exceptions.WorksheetNotFound:
        pass

    customer_records = read_customer_records(spreadsheet)
    account_counts = {}
    for customer in customer_records:
        campaign_name = str(customer.get("campaign", "")).strip()
        if campaign_name:
            account_counts[campaign_name] = account_counts.get(campaign_name, 0) + 1

    if campaign_records:
        for campaign in campaign_records:
            campaign["accountCount"] = account_counts.get(str(campaign.get("name", "")).strip(), 0)
            campaign["dateAdded"] = campaign.get("dateAdded") or campaign.get("startDate") or ""
        return campaign_records

    # Some deployments store campaign uploads only in Customers/category sheets.
    # Derive a registry response so the campaign page and allocation dropdown stay usable.
    derived = {}
    for customer in read_customer_records(spreadsheet):
        campaign_name = str(customer.get("campaign", "")).strip()
        if campaign_name and campaign_name not in derived:
            derived[campaign_name] = {
                "name": campaign_name,
                "type": campaign_name if campaign_name in CATEGORY_HEADERS else "",
                "priority": "",
                "startDate": "",
                "endDate": "",
                "accountCount": account_counts.get(campaign_name, 0),
                "dateAdded": "",
            }
    return list(derived.values())

@app.post("/campaigns")
@app.post("/api/campaigns")
def create_campaign(
    name: str = Body(...), 
    type: str = Body(...), 
    priority: str = Body(...), 
    startDate: str = Body(...), 
    endDate: str = Body(...), 
    customers: List[CustomerUploadModel] = Body(...),
    chunkIndex: int = Body(0)
):
    if len(customers) > 2000:
        raise HTTPException(status_code=413, detail="Each upload request may contain at most 2,000 accounts")
    stage = "Google Sheets connection"
    try:
        sh = get_google_sheet()
        if chunkIndex == 0:
            stage = "campaign registry"
            camp_sheet = get_or_create_campaign_registry(sh)
            if not campaign_registry_has_name(camp_sheet, name):
                camp_sheet.append_row([name, type, priority, startDate, endDate, time.strftime("%Y-%m-%d")])
                campaign_registry_has_name.names.add(name)

        stage = "campaign worksheet"
        campaign_sheet = get_or_create_category_sheet(sh, type)
        rows = [category_row(customer, type) for customer in customers]

        if rows:
            stage = "customer upload"
            campaign_sheet.append_rows(rows)

        return {
            "status": "success",
            "imported": len(customers),
            "campaignSheet": campaign_sheet_name(name),
        }
    except HTTPException:
        raise
    except gspread.exceptions.WorksheetNotFound as error:
        raise HTTPException(status_code=503, detail=f"{stage} worksheet was not found: {error}")
    except Exception as error:
        print(f"Campaign upload failed during {stage}: {error}")
        raise HTTPException(status_code=503, detail=f"Campaign upload failed during {stage}: {error}")


def campaign_registry_has_name(worksheet, campaign_name: str) -> bool:
    names = getattr(campaign_registry_has_name, "names", None)
    if names is None:
        names = {str(value).strip() for value in worksheet.col_values(1) if str(value).strip()}
        campaign_registry_has_name.names = names
    return campaign_name in names


def read_customer_records(spreadsheet) -> List[Dict[str, Any]]:
    """Read the master customer sheet and supplement it with category sheets."""
    records = []
    try:
        master_sheet = spreadsheet.worksheet("Customers")
        records.extend(worksheet_records(master_sheet))
    except gspread.exceptions.WorksheetNotFound:
        pass

    def customer_key(record: Dict[str, Any]):
        phone = str(record.get("phone", "")).strip()
        name = str(record.get("name", "")).strip().lower()
        if phone or name:
            return (phone, name)
        return ("id", str(record.get("id", "")).strip())

    existing_keys = {customer_key(record) for record in records}
    campaign_names = []
    try:
        campaign_registry = spreadsheet.worksheet("Campaigns")
        campaign_names = [
            str(record.get("name", "")).strip()
            for record in worksheet_records(campaign_registry)
            if str(record.get("name", "")).strip()
        ]
    except gspread.exceptions.WorksheetNotFound:
        pass

    for campaign in campaign_names:
        try:
            campaign_sheet = spreadsheet.worksheet(campaign_sheet_name(campaign))
            for record in worksheet_records(campaign_sheet):
                record.setdefault("campaign", campaign)
                key = customer_key(record)
                if key not in existing_keys:
                    records.append(record)
                    existing_keys.add(key)
        except gspread.exceptions.WorksheetNotFound:
            continue

    for category, sheet_names in CATEGORY_SHEET_ALIASES.items():
        for sheet_name in sheet_names:
            try:
                category_sheet = spreadsheet.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                continue
            try:
                for record in worksheet_records(category_sheet):
                    record.setdefault("campaign", category)
                    key = customer_key(record)
                    if key not in existing_keys:
                        records.append(record)
                        existing_keys.add(key)
            except Exception as error:
                print(f"Skipping unavailable category sheet {sheet_name}: {error}")
                continue
    return records


def find_customer_location(spreadsheet, customer_id: str):
    sheets = []
    try:
        sheets.append(spreadsheet.worksheet("Customers"))
    except gspread.exceptions.WorksheetNotFound:
        pass
    for campaign in read_campaign_names(spreadsheet):
        try:
            sheet = spreadsheet.worksheet(campaign_sheet_name(campaign))
            if sheet not in sheets:
                sheets.append(sheet)
        except gspread.exceptions.WorksheetNotFound:
            continue
    for sheet in sheets:
        try:
            cell = sheet.find(str(customer_id).strip())
            return sheet, cell
        except gspread.exceptions.CellNotFound:
            continue
    raise gspread.exceptions.CellNotFound(customer_id)


def read_campaign_names(spreadsheet) -> List[str]:
    try:
        registry = spreadsheet.worksheet("Campaigns")
        return [
            str(record.get("name", "")).strip()
            for record in worksheet_records(registry)
            if str(record.get("name", "")).strip()
        ]
    except gspread.exceptions.WorksheetNotFound:
        return []

@app.get("/customers")
@app.get("/api/customers")
def get_customers(
    agentName: Optional[str] = None,
    offset: int = 0,
    limit: int = 200,
):
    if offset < 0 or limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="offset must be >= 0 and limit must be between 1 and 500")

    try:
        spreadsheet = get_google_sheet()
        records = read_customer_records(spreadsheet)
    except gspread.exceptions.WorksheetNotFound:
        raise HTTPException(status_code=404, detail="Customers worksheet was not found")
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"Google Sheets connection failed: {error}")
    if not records:
        return {"items": [], "offset": offset, "limit": limit, "total": 0, "hasMore": False}

    if agentName:
        cache_key = agentName.strip().lower()
        with _customer_queue_cache_lock:
            cached = _customer_queue_cache.get(cache_key)
            if cached and cached[0] > time.time():
                matches = cached[1]
                return {
                    "items": matches[offset:offset + limit], "offset": offset,
                    "limit": limit, "total": len(matches),
                    "hasMore": offset + limit < len(matches),
                }

        matches = [
            record for record in records
            if str(record.get("agentId", "")).strip() == agentName
            and str(record.get("worked", "")).upper() != "TRUE"
        ]
        with _customer_queue_cache_lock:
            _customer_queue_cache[cache_key] = (time.time() + 20, matches)
        total = len(matches)
        items = matches[offset:offset + limit]
    else:
        total = len(records)
        items = records[offset:offset + limit]

    return {
        "items": items,
        "offset": offset,
        "limit": limit,
        "total": total,
        "hasMore": offset + len(items) < total,
    }


@app.post("/assign")
@app.post("/api/assign")
def assign_customer(assignment: CustomerAssignModel):
    if not can_allocate(assignment.requesterRole):
        raise HTTPException(status_code=403, detail="Only managers can assign accounts")
    spreadsheet = get_google_sheet()
    try:
        sheet, cell = find_customer_location(spreadsheet, assignment.customerId)
    except gspread.exceptions.CellNotFound:
        raise HTTPException(status_code=404, detail="Customer not found")
    headers = sheet.row_values(1)
    agent_column = next((index + 1 for index, header in enumerate(headers) if str(header).strip().lower() in {"agentid", "assignedagent", "agent"}), 0)
    if not agent_column:
        raise HTTPException(status_code=500, detail="Campaign worksheet is missing the agentId column")

    sheet.update_cell(cell.row, agent_column, assignment.agentName)
    with _customer_queue_cache_lock:
        _customer_queue_cache.clear()
    return {"status": "success", "customerId": assignment.customerId, "agentName": assignment.agentName}


# --- ALLOCATION ENGINE ---
@app.post("/distribute")
@app.post("/api/distribute")
def distribute_customers(distribution: DistributionModel):
    if not can_allocate(distribution.requesterRole):
        raise HTTPException(status_code=403, detail="Only managers can assign accounts")
    sh = get_google_sheet()
    try:
        cust_sheet = sh.worksheet(campaign_sheet_name(distribution.campaign))
    except gspread.exceptions.WorksheetNotFound:
        raise HTTPException(status_code=404, detail="Campaign worksheet was not found")
    agent_sheet = sh.worksheet("Agents")
    
    customers = [normalize_record(record) for record in cust_sheet.get_all_records()]
    headers = cust_sheet.row_values(1)
    agent_column = next((index + 1 for index, header in enumerate(headers) if str(header).strip().lower() in {"agentid", "assignedagent", "agent"}), 8)
    assigned_agents = {
        str(customer.get("agentId", "")).strip()
        for customer in read_customer_records(sh)
        if str(customer.get("agentId", "")).strip()
        and str(customer.get("campaign", "")).strip()
    }
    agents = [normalize_record(record) for record in agent_sheet.get_all_records()
        if str(record.get("status", record.get("Status", ""))).strip().lower() in {"clocked in", "online"}
        and str(record.get("role", record.get("Role", ""))).strip().lower() == "control agent"
        and str(record.get("name", record.get("Name", ""))).strip() not in assigned_agents
        and record.get("name", record.get("Name")) in distribution.selectedAgents]
    
    if not agents:
        raise HTTPException(status_code=400, detail="No active agents found")
    
    unassigned = [
        i + 2 for i, c in enumerate(customers) 
        if c.get("campaign") == distribution.campaign and not c.get("agentId") and str(c.get("worked")).upper() != "TRUE"
    ]
    
    if not unassigned:
        return {"status": "info", "message": "No unassigned customers remaining"}
    
    cells_to_update = []
    agent_idx = 0
    assigned_count = 0
    
    for row_idx in unassigned:
        assigned_agent = agents[agent_idx].get("name")
        cells_to_update.append(gspread.Cell(row_idx, agent_column, assigned_agent))
        assigned_count += 1
        agent_idx = (agent_idx + 1) % len(agents)
        
    cust_sheet.update_cells(cells_to_update)
    with _customer_queue_cache_lock:
        _customer_queue_cache.clear()
        
    return {"status": "success", "assignedCount": assigned_count}


# --- DISPOSITION LOGGING ---
@app.post("/disposition")
@app.post("/api/disposition")
def submit_disposition(disp: DispositionModel):
    sh = get_google_sheet()
    agent_sheet = sh.worksheet("Agents")
    
    # 1. Update Customer Record
    try:
        cust_sheet, cust_cell = find_customer_location(sh, disp.customerId)
        r = cust_cell.row
        headers = cust_sheet.row_values(1)
        columns = {str(header).strip().lower(): index + 1 for index, header in enumerate(headers)}
        customer_updates = []
        for key, value in (("worked", "TRUE"), ("outcome", disp.outcome), ("status", disp.status), ("businessstatus", disp.businessStatus), ("ptpamount", disp.amountRec), ("ptptime", disp.ptpTime)):
            column = columns.get(key)
            if column:
                customer_updates.append(gspread.Cell(r, column, value))
        cust_sheet.update_cells(customer_updates)
    except gspread.exceptions.CellNotFound:
        pass
        
    # 2. Update Agent Metrics
    try:
        agent_cell = agent_sheet.find(disp.agentName)
        ar = agent_cell.row
        calls = int(agent_sheet.cell(ar, 5).value or 0) + 1
        connected = int(agent_sheet.cell(ar, 6).value or 0) + (1 if disp.outcome == "Answered" else 0)
        conversion = float(agent_sheet.cell(ar, 7).value or 0) + disp.amountRec
        
        agent_updates = [
            gspread.Cell(ar, 5, calls),
            gspread.Cell(ar, 6, connected),
            gspread.Cell(ar, 7, conversion)
        ]
        agent_sheet.update_cells(agent_updates)
    except gspread.exceptions.CellNotFound:
        pass
        
    with _customer_queue_cache_lock:
        _customer_queue_cache.clear()
    return {"status": "success"}

# --- LOCALHOST STATIC FILE SERVING ---
if os.getenv("VERCEL") is None:
    app.mount("/", StaticFiles(directory=".", html=True), name="static")