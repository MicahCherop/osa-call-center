import os
import json
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Body
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

# Load Google Credentials from Environment Variable in Vercel
SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_google_sheet():
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.getenv("1VmLCg_6iY0QsjPbDjgDRNugFyyNRWABtPIASGDepZeU")
    sheet_name = os.getenv("GOOGLE_SHEET_NAME", "OSA_Call_Center_DB")
    
    if not creds_json:
        raise HTTPException(status_code=500, detail="Google credentials environment variable missing.")
    
    try:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPE)
        client = gspread.authorize(creds)
        
        # 1. Prefer opening directly by ID if GOOGLE_SHEET_ID is set in Vercel
        if sheet_id:
            return client.open_by_key(sheet_id)
        
        # 2. Fall back to opening by sheet name
        return client.open(sheet_name)

    except gspread.exceptions.SpreadsheetNotFound:
        raise HTTPException(
            status_code=500, 
            detail="Spreadsheet not found. Ensure the Google Sheet is shared as 'Editor' with the 'client_email' in your service account JSON."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Google Sheets Auth Error: {str(e)}")

# Pydantic Schemas
class AgentModel(BaseModel):
    name: str
    team: str
    status: str = "Active"

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

# Endpoints

# Add root routes so the browser doesn't show "Not Found"
@app.get("/")
@app.get("/api")
def read_root():
    return {"message": "FastAPI Server is running successfully on Vercel!"}

@app.get("/api/health")
@app.get("/health")
def health_check():
    return {"status": "ok", "message": "Call Center API is running"}

# --- AGENTS ---
@app.get("/api/agents")
@app.get("/agents")
def get_agents():
    sheet = get_google_sheet().worksheet("Agents")
    return sheet.get_all_records()

@app.post("/api/agents")
@app.post("/agents")
def add_agent(agent: AgentModel):
    sheet = get_google_sheet().worksheet("Agents")
    sheet.append_row([agent.name, agent.team, agent.status, 0, 0, 0, 0])
    return {"status": "success", "message": f"Agent {agent.name} added"}

@app.put("/api/agents/status")
@app.put("/agents/status")
def update_agent_status(name: str = Body(...), status: str = Body(...)):
    sheet = get_google_sheet().worksheet("Agents")
    cell = sheet.find(name)
    if not cell:
        raise HTTPException(status_code=404, detail="Agent not found")
    sheet.update_cell(cell.row, 3, status)
    return {"status": "success"}

# --- CAMPAIGNS & CUSTOMERS ---
@app.get("/api/campaigns")
@app.get("/campaigns")
def get_campaigns():
    sheet = get_google_sheet().worksheet("Campaigns")
    return sheet.get_all_records()

@app.post("/api/campaigns")
@app.post("/campaigns")
def create_campaign(name: str = Body(...), type: str = Body(...), priority: str = Body(...), startDate: str = Body(...), endDate: str = Body(...), customers: List[CustomerUploadModel] = Body(...)):
    sh = get_google_sheet()
    
    # Save campaign
    camp_sheet = sh.worksheet("Campaigns")
    camp_sheet.append_row([name, type, priority, startDate, endDate])
    
    # Save customers
    cust_sheet = sh.worksheet("Customers")
    rows = []
    for c in customers:
        rows.append([c.id, c.name, c.phone, c.branch, c.sector, c.balance, name, "", "FALSE", "", ""])
    cust_sheet.append_rows(rows)
    
    return {"status": "success", "imported": len(customers)}

@app.get("/api/customers")
@app.get("/customers")
def get_customers(agentName: Optional[str] = None):
    sheet = get_google_sheet().worksheet("Customers")
    records = sheet.get_all_records()
    if agentName:
        records = [r for r in records if str(r.get("agentId")) == agentName and str(r.get("worked")).upper() != "TRUE"]
    return records

# --- ALLOCATION ENGINE ---
@app.post("/api/distribute")
@app.post("/distribute")
def distribute_customers(campaign: str = Body(...)):
    sh = get_google_sheet()
    cust_sheet = sh.worksheet("Customers")
    agent_sheet = sh.worksheet("Agents")
    
    customers = cust_sheet.get_all_records()
    agents = [a for a in agent_sheet.get_all_records() if a.get("status") == "Active"]
    
    if not agents:
        raise HTTPException(status_code=400, detail="No active agents found")
    
    unassigned = [i + 2 for i, c in enumerate(customers) if c.get("campaign") == campaign and not c.get("agentId") and str(c.get("worked")).upper() != "TRUE"]
    
    if not unassigned:
        return {"status": "info", "message": "No unassigned customers remaining"}
    
    agent_idx = 0
    assigned_count = 0
    for row_idx in unassigned:
        assigned_agent = agents[agent_idx]["name"]
        cust_sheet.update_cell(row_idx, 8, assigned_agent) # Col 8 = agentId
        assigned_count += 1
        agent_idx = (agent_idx + 1) % len(agents)
        
    return {"status": "success", "assignedCount": assigned_count}

# --- DISPOSITION ---
@app.post("/api/disposition")
@app.post("/disposition")
def submit_disposition(disp: DispositionModel):
    sh = get_google_sheet()
    cust_sheet = sh.worksheet("Customers")
    agent_sheet = sh.worksheet("Agents")
    
    # 1. Update Customer Record
    cust_cell = cust_sheet.find(str(disp.customerId))
    if cust_cell:
        r = cust_cell.row
        cust_sheet.update_cell(r, 9, "TRUE")       # worked
        cust_sheet.update_cell(r, 10, disp.outcome) # outcome
        cust_sheet.update_cell(r, 11, disp.status)  # status
        
    # 2. Update Agent Metrics
    agent_cell = agent_sheet.find(disp.agentName)
    if agent_cell:
        ar = agent_cell.row
        calls = int(agent_sheet.cell(ar, 5).value or 0) + 1
        connected = int(agent_sheet.cell(ar, 6).value or 0) + (1 if disp.outcome == "Answered" else 0)
        conversion = float(agent_sheet.cell(ar, 7).value or 0) + disp.amountRec
        
        agent_sheet.update_cell(ar, 5, calls)
        agent_sheet.update_cell(ar, 6, connected)
        agent_sheet.update_cell(ar, 7, conversion)
        
    return {"status": "success"}