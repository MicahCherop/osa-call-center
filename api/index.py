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

class CustomerUploadModel(BaseModel):
    id: int
    name: str
    phone: str
    branch: str
    sector: str
    balance: str
    campaign: str


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
    return sheet.get_all_records()

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
    return sheet.get_all_records()

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
    
    # Save campaign metadata
    camp_sheet = sh.worksheet("Campaigns")
    camp_sheet.append_row([name, type, priority, startDate, endDate])
    
    # Save imported customers
    cust_sheet = sh.worksheet("Customers")
    rows = []
    for c in customers:
        rows.append([c.id, c.name, c.phone, c.branch, c.sector, c.balance, name, "", "FALSE", "", ""])
    cust_sheet.append_rows(rows)
    
    return {"status": "success", "imported": len(customers)}

@app.get("/customers")
@app.get("/api/customers")
def get_customers(agentName: Optional[str] = None):
    sheet = get_google_sheet().worksheet("Customers")
    records = sheet.get_all_records()
    if agentName:
        records = [
            r for r in records 
            if str(r.get("agentId")) == agentName and str(r.get("worked")).upper() != "TRUE"
        ]
    return records


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
        cells_to_update.append(gspread.Cell(row_idx, 8, assigned_agent))  # Col 8 = agentId
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
        
    return {"status": "success"}

# --- LOCALHOST STATIC FILE SERVING ---
if os.getenv("VERCEL") is None:
    app.mount("/", StaticFiles(directory=".", html=True), name="static")