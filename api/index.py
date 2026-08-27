import os
import json
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="OSA Call Center API")

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
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    
    if not creds_json:
        raise HTTPException(status_code=500, detail="Google credentials environment variable missing.")
    
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
    client = gspread.authorize(creds)
    
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "1VmLCg_6iY0QsjPbDjgDRNugFyyNRWABtPIASGDepZeU")
    
    try:
        return client.open_by_key(sheet_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open sheet: {str(e)}")

# --- PYDANTIC SCHEMAS ---
class UserCreateModel(BaseModel):
    name: str
    email: str
    role: str
    status: str = "Active"

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

class CustomerUploadModel(BaseModel):
    id: int
    name: str
    phone: str
    branch: str
    sector: str
    balance: str
    campaign: str


# ==========================================
# MOCK DEMO DATA
# ==========================================
demo_agents = [
    {"name": "Joseph Cherop", "email": "joseph@osa.com", "role": "Ops Manager", "status": "Active", "campaign": "All", "callsMade": 0, "connected": 0, "conversion": 0},
    {"name": "Sarah Wanjiku", "email": "sarah@osa.com", "role": "Team Leader", "status": "Active", "campaign": "All", "callsMade": 0, "connected": 0, "conversion": 0},
    {"name": "David Ochieng", "email": "david@osa.com", "role": "Control Agent", "status": "Clocked In", "campaign": "DD1-DD7 Collections", "callsMade": 45, "connected": 18, "conversion": 150000},
    {"name": "Mercy Mutisya", "email": "mercy@osa.com", "role": "Control Agent", "status": "Idle", "campaign": "Dormant Reactivation", "callsMade": 32, "connected": 8, "conversion": 45000},
    {"name": "Brian Kipkorir", "email": "brian@osa.com", "role": "Control Agent", "status": "Offline", "campaign": "Active No Loan", "callsMade": 0, "connected": 0, "conversion": 0},
    {"name": "Grace Achieng", "email": "grace@osa.com", "role": "Control Agent", "status": "Clocked In", "campaign": "DD1-DD7 Collections", "callsMade": 55, "connected": 22, "conversion": 210000},
]

demo_campaigns = [
    {"name": "DD1-DD7 Collections", "type": "defaulted", "priority": "high", "startDate": "2026-08-01", "endDate": "2026-08-31"},
    {"name": "Dormant Reactivation", "type": "dormant", "priority": "medium", "startDate": "2026-08-15", "endDate": "2026-09-15"},
    {"name": "Active No Loan", "type": "active_no_loan", "priority": "low", "startDate": "2026-08-20", "endDate": "2026-09-20"}
]

demo_customers = [
    {"id": 1, "name": "John Ndungu", "phone": "0712345678", "campaign": "DD1-DD7 Collections", "agentId": "David Ochieng", "outcome": "Answered", "status": "Promise to Pay (PTP)", "balance": "45000", "branch": "Nairobi CBD", "sector": "Retail", "worked": "TRUE", "pendingReschedule": False},
    {"id": 2, "name": "Alice Njoroge", "phone": "0723456789", "campaign": "Dormant Reactivation", "agentId": "Mercy Mutisya", "outcome": "Unanswered", "status": "Pending Callback", "balance": "0", "branch": "Westlands", "sector": "Tech", "worked": "TRUE", "pendingReschedule": False},
    {"id": 3, "name": "Peter Kamau", "phone": "0734567890", "campaign": "DD1-DD7 Collections", "agentId": "David Ochieng", "outcome": "", "status": "", "balance": "12500", "branch": "Mombasa", "sector": "Transport", "worked": "FALSE", "pendingReschedule": False},
    {"id": 4, "name": "Lucy Atieno", "phone": "0745678901", "campaign": "DD1-DD7 Collections", "agentId": "David Ochieng", "outcome": "", "status": "", "balance": "80000", "branch": "Kisumu", "sector": "Agriculture", "worked": "FALSE", "pendingReschedule": True}, 
    {"id": 5, "name": "Kevin Mbugua", "phone": "0756789012", "campaign": "Active No Loan", "agentId": "", "outcome": "", "status": "", "balance": "0", "branch": "Nakuru", "sector": "Education", "worked": "FALSE", "pendingReschedule": False},
    {"id": 6, "name": "Faith Wanjala", "phone": "0767890123", "campaign": "DD1-DD7 Collections", "agentId": "", "outcome": "", "status": "", "balance": "35000", "branch": "Eldoret", "sector": "Wholesale", "worked": "FALSE", "pendingReschedule": False},
    {"id": 7, "name": "Samuel Omondi", "phone": "0778901234", "campaign": "Dormant Reactivation", "agentId": "", "outcome": "", "status": "", "balance": "0", "branch": "Nairobi CBD", "sector": "Retail", "worked": "FALSE", "pendingReschedule": False}
]

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
        
        # 1. DOMAIN FIREWALL
        if not email_lower.endswith("@4g-capital.com"):
            raise HTTPException(status_code=403, detail="Unauthorized network. Only corporate @4g-capital.com accounts are permitted.")
            
        sheet = get_google_sheet().worksheet("Agents")
        agents = sheet.get_all_records()
        
        # 2. DATABASE CHECK
        user = next((a for a in agents if str(a.get("Email", a.get("email", ""))).lower().strip() == email_lower), None)
        
        if not user:
            raise HTTPException(status_code=403, detail="ACCESS DENIED: Your email is not registered in the active users database. Contact your Ops Manager.")
            
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


# --- GET DATA (USING MOCK DATA FOR DEMO) ---
@app.get("/api/agents")
@app.get("/agents")
async def get_agents():
    return {"data": demo_agents}

@app.get("/api/campaigns")
@app.get("/campaigns")
async def get_campaigns():
    return {"data": demo_campaigns}

@app.get("/api/customers")
@app.get("/customers")
async def get_customers():
    return {"data": demo_customers}


# --- USER MANAGEMENT (ADMIN ENDPOINTS) ---
@app.post("/api/users/add")
def add_user(user: UserCreateModel):
    try:
        sheet = get_google_sheet().worksheet("Agents")
        existing_users = sheet.get_all_records()
        for u in existing_users:
            if str(u.get("Email", "")).lower().strip() == user.email.lower().strip():
                raise HTTPException(status_code=400, detail="A user with this email already exists.")
        
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
                row_idx = idx + 2  
                break
                
        if not row_idx:
            raise HTTPException(status_code=404, detail="User not found in database.")
            
        headers = sheet.row_values(1)
        name_col = headers.index("Name") + 1 if "Name" in headers else 2
        role_col = headers.index("Role") + 1 if "Role" in headers else 4
        
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
        sheet.update_cell(cell.row, 1, status) 
        return {"status": "success"}
    except gspread.exceptions.CellNotFound:
        raise HTTPException(status_code=404, detail="Agent not found in database")


# --- CAMPAIGNS POST ROUTE ---
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
    sh = get_google_sheet()
    
    camp_sheet = sh.worksheet("Campaigns")
    camp_sheet.append_row([name, type, priority, startDate, endDate])
    
    cust_sheet = sh.worksheet("Customers")
    rows = []
    for c in customers:
        rows.append([c.id, c.name, c.phone, c.branch, c.sector, c.balance, name, "", "FALSE", "", ""])
    cust_sheet.append_rows(rows)
    
    return {"status": "success", "imported": len(customers)}


# --- ALLOCATION ENGINE ---
@app.post("/distribute")
@app.post("/api/distribute")
def distribute_customers(campaign: str = Body(...)):
    sh = get_google_sheet()
    cust_sheet = sh.worksheet("Customers")
    agent_sheet = sh.worksheet("Agents")
    
    customers = cust_sheet.get_all_records()
    agents = [
        a for a in agent_sheet.get_all_records() 
        if str(a.get("Status", a.get("status", ""))).lower() == "active"
    ]
    
    if not agents:
        raise HTTPException(status_code=400, detail="No active agents found")
    
    unassigned = [
        i + 2 for i, c in enumerate(customers) 
        if c.get("campaign") == campaign and not c.get("agentId") and str(c.get("worked")).upper() != "TRUE"
    ]
    
    if not unassigned:
        return {"status": "info", "message": "No unassigned customers remaining"}
    
    cells_to_update = []
    agent_idx = 0
    assigned_count = 0
    
    for row_idx in unassigned:
        assigned_agent = agents[agent_idx].get("Name", agents[agent_idx].get("name"))
        cells_to_update.append(gspread.Cell(row_idx, 8, assigned_agent))  
        assigned_count += 1
        agent_idx = (agent_idx + 1) % len(agents)
        
    cust_sheet.update_cells(cells_to_update)
    return {"status": "success", "assignedCount": assigned_count}


# --- DISPOSITION LOGGING ---
@app.post("/disposition")
@app.post("/api/disposition")
def submit_disposition(disp: DispositionModel):
    sh = get_google_sheet()
    cust_sheet = sh.worksheet("Customers")
    agent_sheet = sh.worksheet("Agents")
    
    try:
        cust_cell = cust_sheet.find(str(disp.customerId))
        r = cust_cell.row
        customer_updates = [
            gspread.Cell(r, 9, "TRUE"),        
            gspread.Cell(r, 10, disp.outcome), 
            gspread.Cell(r, 11, disp.status)   
        ]
        cust_sheet.update_cells(customer_updates)
    except gspread.exceptions.CellNotFound:
        pass
        
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
        
    return {"status": "success"}

# --- LOCALHOST STATIC FILE SERVING ---
if os.getenv("VERCEL") is None:
    app.mount("/", StaticFiles(directory=".", html=True), name="static")