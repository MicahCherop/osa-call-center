import os
import json
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

    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    
    if not creds_json:
        raise HTTPException(status_code=500, detail="Google credentials environment variable missing.")
    
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
    client = gspread.authorize(creds)
    
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "1VmLCg_6iY0QsjPbDjgDRNugFyyNRWABtPIASGDepZeU")
    
    try:
        with _sheet_cache_lock:
            if _sheet_cache is None:
                _sheet_cache = client.open_by_key(sheet_id)
            return _sheet_cache
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open sheet: {str(e)}")


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
            }
            if compact_key in aliases:
                normalized[aliases[compact_key]] = value
                continue

            words = "".join(char if char.isalnum() else " " for char in str(key)).split()
            camel_key = words[0].lower() + "".join(word.title() for word in words[1:])
            normalized[camel_key] = value
    return normalized


def records_from_rows(rows: List[List[Any]], headers: List[str]) -> List[Dict[str, Any]]:
    return [
        normalize_record(dict(zip(headers, row + [""] * (len(headers) - len(row)))))
        for row in rows
        if any(str(value).strip() for value in row)
    ]


def column_letter(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


CUSTOMER_HEADERS = [
    "id", "name", "phone", "branch", "sector", "balance", "campaign",
    "agentId", "worked", "outcome", "status", "dueDate", "station", "stations",
    "pair", "disbAmount", "totalPaid"
]

CATEGORY_HEADERS = {
    "Defaulted": ["Name", "Mobile No", "Due Date", "Station", "Stations", "Pair", "Sector", "Disb Amount", "Total Paid", "Balance"],
    "Upcoming Dues": ["Name", "Mobile No", "Due Date", "Station", "Stations", "Pair", "Sector", "Disb Amount", "Total Paid", "Balance"],
    "Active No Loan": ["Name", "Mobile No", "Due Date", "Station", "Stations", "Pair", "Sector", "Disb Amount"],
    "Dormant": ["Name", "Mobile No", "Due Date", "Station", "Stations", "Pair", "Sector", "Disb Amount"],
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
        if worksheet.row_values(1) != headers:
            worksheet.update("A1", [headers])
        return worksheet
    except gspread.exceptions.WorksheetNotFound:
        with _category_sheet_lock:
            try:
                worksheet = spreadsheet.worksheet(title)
                if worksheet.col_count < len(headers):
                    worksheet.resize(cols=len(headers))
                if worksheet.row_values(1) != headers:
                    worksheet.update("A1", [headers])
                return worksheet
            except gspread.exceptions.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(
                    title=title,
                    rows=1000,
                    cols=len(headers),
                )
                worksheet.append_row(headers)
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
    customerId: int
    outcome: str
    status: str
    amountRec: float = 0.0
    agentName: str
    comments: str = ""
    businessStatus: str = ""


class CustomerAssignModel(BaseModel):
    customerId: int
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


def category_row(customer: CustomerUploadModel, category: str) -> List[Any]:
    values = {
        "Name": customer.name,
        "Mobile No": customer.phone,
        "Due Date": customer.dueDate,
        "Station": customer.station,
        "Stations": customer.stations,
        "Pair": customer.pair,
        "Sector": customer.sector,
        "Disb Amount": customer.disbAmount,
        "Total Paid": customer.totalPaid,
        "Balance": customer.balance,
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
    return {"status": "ok", "message": "Call Center API is running"}


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
    sheet = get_google_sheet().worksheet("Agents")
    headers = sheet.row_values(1)
    if not headers:
        return []
    rows = sheet.get_values(f"A2:{column_letter(len(headers))}{sheet.row_count}")
    return records_from_rows(rows, headers)

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
        # Ensure your status column is correctly mapped (using Col 1 as standard, change if your sheet differs)
        sheet.update_cell(cell.row, 1, status) 
        return {"status": "success"}
    except gspread.exceptions.CellNotFound:
        raise HTTPException(status_code=404, detail="Agent not found in database")


# --- CAMPAIGNS & CUSTOMERS ---
@app.get("/campaigns")
@app.get("/api/campaigns")
def get_campaigns():
    sheet = get_google_sheet().worksheet("Campaigns")
    headers = sheet.row_values(1)
    if not headers:
        return []
    rows = sheet.get_values(f"A2:{column_letter(len(headers))}{sheet.row_count}")
    return records_from_rows(rows, headers)

@app.post("/campaigns")
@app.post("/api/campaigns")
def create_campaign(
    name: str = Body(...), 
    type: str = Body(...), 
    priority: str = Body(...), 
    startDate: str = Body(...), 
    endDate: str = Body(...), 
    customers: List[CustomerUploadModel] = Body(...)
):
    try:
        sh = get_google_sheet()
        camp_sheet = sh.worksheet("Campaigns")
        existing_campaigns = camp_sheet.col_values(1)

        if name not in existing_campaigns:
            camp_sheet.append_row([name, type, priority, startDate, endDate])

        cust_sheet = sh.worksheet("Customers")
        ensure_customer_headers(cust_sheet)
        rows = [[
            c.id, c.name, c.phone, c.branch, c.sector, c.balance, name, "", "FALSE", "", "",
            c.dueDate, c.station, c.stations, c.pair, c.disbAmount, c.totalPaid,
        ] for c in customers]

        if rows:
            cust_sheet.append_rows(rows)
            category_sheet = get_or_create_category_sheet(sh, type)
            category_sheet.append_rows([category_row(customer, type) for customer in customers])

        return {
            "status": "success",
            "imported": len(customers),
            "categorySheet": category_sheet_name(type),
        }
    except HTTPException:
        raise
    except gspread.exceptions.WorksheetNotFound as error:
        raise HTTPException(status_code=503, detail=f"Required Google worksheet was not found: {error}")
    except Exception as error:
        print(f"Campaign upload failed: {error}")
        raise HTTPException(status_code=503, detail="Google Sheets upload failed. Check API logs and spreadsheet permissions.")


def read_customer_records(spreadsheet) -> List[Dict[str, Any]]:
    """Read the master customer sheet and supplement it with category sheets."""
    records = []
    try:
        master_sheet = spreadsheet.worksheet("Customers")
        master_headers = master_sheet.row_values(1)
        if master_headers:
            end_column = column_letter(len(master_headers))
            rows = master_sheet.get_values(f"A2:{end_column}{master_sheet.row_count}")
            records.extend(records_from_rows(rows, master_headers))
    except gspread.exceptions.WorksheetNotFound:
        pass

    def customer_key(record: Dict[str, Any]):
        phone = str(record.get("phone", "")).strip()
        name = str(record.get("name", "")).strip().lower()
        if phone or name:
            return (phone, name)
        return ("id", str(record.get("id", "")).strip())

    existing_keys = {customer_key(record) for record in records}
    for category, sheet_names in CATEGORY_SHEET_ALIASES.items():
        for sheet_name in sheet_names:
            try:
                category_sheet = spreadsheet.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                continue
            try:
                headers = category_sheet.row_values(1)
                if not headers:
                    continue
                end_column = column_letter(len(headers))
                rows = category_sheet.get_values(f"A2:{end_column}{category_sheet.row_count}")
                for record in records_from_rows(rows, headers):
                    record.setdefault("campaign", category)
                    key = customer_key(record)
                    if key not in existing_keys:
                        records.append(record)
                        existing_keys.add(key)
            except Exception as error:
                print(f"Skipping unavailable category sheet {sheet_name}: {error}")
                continue
    return records

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
    if assignment.requesterRole.strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="Only Admin users can assign accounts")
    sheet = get_google_sheet().worksheet("Customers")
    cell = sheet.find(str(assignment.customerId))
    headers = sheet.row_values(1)
    try:
        agent_column = headers.index("agentId") + 1
    except ValueError:
        try:
            agent_column = headers.index("AgentId") + 1
        except ValueError:
            raise HTTPException(status_code=500, detail="Customers sheet is missing the agentId column")

    sheet.update_cell(cell.row, agent_column, assignment.agentName)
    with _customer_queue_cache_lock:
        _customer_queue_cache.clear()
    return {"status": "success", "customerId": assignment.customerId, "agentName": assignment.agentName}


# --- ALLOCATION ENGINE ---
@app.post("/distribute")
@app.post("/api/distribute")
def distribute_customers(distribution: DistributionModel):
    if distribution.requesterRole.strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="Only Admin users can assign accounts")
    sh = get_google_sheet()
    cust_sheet = sh.worksheet("Customers")
    agent_sheet = sh.worksheet("Agents")
    
    customers = [normalize_record(record) for record in cust_sheet.get_all_records()]
    agents = [normalize_record(record) for record in agent_sheet.get_all_records()
        if str(record.get("status", record.get("Status", ""))).strip().lower() in {"active", "clocked in", "online"}
        and str(record.get("role", record.get("Role", ""))).strip().lower() == "control agent"
        and not str(record.get("campaign", record.get("Campaign", ""))).strip()
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
        cells_to_update.append(gspread.Cell(row_idx, 8, assigned_agent))  # Col 8 = agentId
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
    cust_sheet = sh.worksheet("Customers")
    agent_sheet = sh.worksheet("Agents")
    
    # 1. Update Customer Record
    try:
        cust_cell = cust_sheet.find(str(disp.customerId))
        r = cust_cell.row
        customer_updates = [
            gspread.Cell(r, 9, "TRUE"),        # worked
            gspread.Cell(r, 10, disp.outcome), # outcome
            gspread.Cell(r, 11, disp.status)   # status
        ]
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