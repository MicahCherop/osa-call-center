
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

# Pydantic Schemas
class AgentModel(BaseModel):
    name: str
    email: str
    role: str
    password: str = ""
    status: str = "Active"

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

# --- AUTHENTICATION & AGENTS ---
@app.post("/login")
@app.post("/api/login")
def login(creds: LoginModel):
    try:
        # Require company email domain
        if not creds.email.endswith("@4g-capital.com"):
            raise HTTPException(status_code=403, detail="Invalid domain. Must use a @4g-capital.com email.")
            
        sheet = get_google_sheet().worksheet("Agents")
        agents = sheet.get_all_records()
        
        # Check if user exists in the Agents sheet
        user = next((a for a in agents if str(a.get("Email", a.get("email", ""))).lower() == creds.email.lower()), None)
        
        if user:
            role = user.get("Role", user.get("role", "Control Agent"))
            name = user.get("Name", user.get("name", "Unknown"))
        else:
            # Default fallback for new valid domain users
            role = "Control Agent"
            name = creds.email.split("@")[0].replace(".", " ").title()

        return {
            "success": True,
            "email": creds.email,
            "role": role,
            "name": name
        }
    except Exception as e:
        # Fallback to ensure UI doesn't break if Google Sheets fails during login
        return {
            "success": True,
            "email": creds.email,
            "role": "Control Agent",
            "name": creds.email.split("@")[0].title()
        }

@app.get("/agents")
@app.get("/api/agents")
def get_agents():
    sheet = get_google_sheet().worksheet("Agents")
    return sheet.get_all_records()

@app.post("/agents")
@app.post("/api/agents")
def add_agent(agent: AgentModel):
    sheet = get_google_sheet().worksheet("Agents")
    row_data = [agent.status, agent.name, agent.email, agent.role, agent.password]
    sheet.append_row(row_data)
    return {"status": "success", "message": f"Agent {agent.name} added successfully"}

@app.put("/agents/status")
@app.put("/api/agents/status")
def update_agent_status(name: str = Body(...), status: str = Body(...)):
    sheet = get_google_sheet().worksheet("Agents")
    try:
        cell = sheet.find(name)
        if not cell:
            raise HTTPException(status_code=404, detail="Agent not found")
        sheet.update_cell(cell.row, 3, status)
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
# This serves your HTML/JS/CSS locally, but gets out of the way on Vercel's live servers
if os.getenv("VERCEL") is None:
    app.mount("/", StaticFiles(directory=".", html=True), name="static")