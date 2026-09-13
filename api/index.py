import os
from dotenv import load_dotenv
load_dotenv()
import time
import gspread
import json
import urllib.parse
from google.oauth2.service_account import Credentials
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TypeVar

from fastapi import Body, FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from supabase import Client, create_client
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from fastapi.responses import RedirectResponse

try:  # Vercel loads this file as a standalone module ("index"), so use a plain import.
    from priority import get_weights, rank_candidates, candidate_key
    import customer360
    import followups as followups_module
except ImportError:  # Local/uvicorn runs this as the "api" package ("api.index").
    from api.priority import get_weights, rank_candidates, candidate_key
    from api import customer360
    from api import followups as followups_module


T = TypeVar("T")

app = FastAPI(title="OSA Call Center API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
_supabase: Optional[Client] = None
security = HTTPBearer()

GOOGLE_CLIENT_ID = "106325673835-4moq7op50u80oet78ln84t241a88a200.apps.googleusercontent.com"


def get_supabase() -> Client:
    global _supabase
    if _supabase is not None:
        return _supabase
    url, key = os.getenv("SUPABASE_URL", "").strip(), os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise HTTPException(503, "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
    _supabase = create_client(url, key)
    return _supabase


def db_error(error: Exception) -> HTTPException:
    print(f"[db_error] {error}")
    return HTTPException(503, "A database request failed. Please try again or contact support.")


_TRANSIENT_MARKERS = ("timeout", "temporarily unavailable", "connection", "reset", "429", "502", "503", "504")


def with_retry(operation: Callable[[], T], attempts: int = 3, base_delay: float = 0.4) -> T:
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:
            last_error = error
            if attempt == attempts - 1 or not any(marker in str(error).lower() for marker in _TRANSIENT_MARKERS):
                raise
            time.sleep(base_delay * (2 ** attempt))
    raise last_error


# --- CORE AUTHENTICATION DEPENDENCY ---
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """Verifies the Google JWT and fetches the user's true authoritative role from Supabase."""
    try:
        idinfo = id_token.verify_oauth2_token(credentials.credentials, google_requests.Request(), GOOGLE_CLIENT_ID)
        email = idinfo.get("email", "").lower()
    except ValueError:
        raise HTTPException(401, "Invalid or expired authentication token.")
    
    if not email.endswith("@4g-capital.com"):
        raise HTTPException(403, "Only corporate @4g-capital.com accounts are permitted.")

    try:
        result = get_supabase().table("agents").select("*").eq("email", email).limit(1).execute()
    except Exception as error:
        raise db_error(error)

    if not result.data:
        raise HTTPException(403, "ACCESS DENIED: Your email is not registered in the active users database.")
    
    agent = result.data[0]
    if str(agent.get("status", "")).lower() == "inactive":
        raise HTTPException(403, "ACCOUNT SUSPENDED: Your access has been revoked.")
        
    return agent


# --- UTILITIES ---
def text(value: Any) -> str:
    return str(value or "").strip()

def optional_number(value: Any) -> Optional[float]:
    try:
        return float(text(value).replace(",", "")) if text(value) else None
    except ValueError:
        return None

def optional_integer(value: Any) -> Optional[int]:
    number = optional_number(value)
    return int(number) if number is not None else None

def optional_date(value: Any) -> Optional[str]:
    try:
        return date.fromisoformat(text(value)).isoformat() if text(value) else None
    except ValueError:
        return None

def optional_datetime(value: Any) -> Optional[str]:
    try:
        return datetime.fromisoformat(text(value).replace("Z", "+00:00")).isoformat() if text(value) else None
    except ValueError:
        return None

def agent_response(row: Dict[str, Any]) -> Dict[str, Any]:
    # Views are returned as lists in Supabase. Safely extract the first object.
    perf_data = row.get("performance")
    if isinstance(perf_data, list):
        performance = perf_data[0] if len(perf_data) > 0 else {}
    elif isinstance(perf_data, dict):
        performance = perf_data
    else:
        performance = {}

    return {
        "id": row["id"], 
        "name": row["name"], 
        "email": row["email"], 
        "role": row["role"], 
        "status": row["status"], 
        "callsMade": int(performance.get("calls_made") or 0), 
        "connected": int(performance.get("connected") or 0), 
        "conversion": float(performance.get("conversion") or 0)
    }

def customer_response(row: Dict[str, Any]) -> Dict[str, Any]:
    campaign, agent = row.get("campaign") or {}, row.get("assigned_agent") or {}
    return {
        "id": row["customer_id"], 
        "name": row["name"], 
        "phone": row["phone"], 
        "branch": row["branch"], 
        "sector": row["sector"], 
        "balance": row["balance"] or "", 
        "campaign": campaign.get("name", ""), 
        "agentId": agent.get("name", ""), 
        "worked": "TRUE" if row["worked"] else "FALSE", 
        "outcome": row["outcome"], 
        "status": row["status"], 
        "dueDate": row["due_date"] or "", 
        "pair": row["pair"], 
        "disbAmount": row["disb_amount"] or "", 
        "totalPaid": row["total_paid"] or "", 
        "businessStatus": row["business_status"], 
        "ptpAmount": row["ptp_amount"] or "", 
        "ptpTime": row["ptp_time"], 
        "feedback": row["feedback"], 
        "daysInactive": row["days_inactive"] or "", 
        "daysDormant": row["days_dormant"] or "", 
        "loyalty": row["loyalty"], 
        "lastLoanAmount": row["last_loan_amount"] or "", 
        "sourceData": row.get("source_data") or {},
        "updatedAt": row.get("updated_at") or ""   # <--- ADD THIS LINE
    }


# --- MODELS (requesterRole removed - handled securely by JWT) ---
class UserCreateModel(BaseModel):
    name: str
    email: str
    role: str
    status: str = "Active"

class UserEditModel(BaseModel):
    email: str
    name: str
    role: str
    status: str = "Active"

class UserDeleteModel(BaseModel):
    email: str

class LoginModel(BaseModel):
    token: str # Replaced email with secure token

class DispositionModel(BaseModel):
    customerId: str
    outcome: str
    status: str
    amountRec: float = 0.0
    agentName: str
    comments: str = ""
    businessStatus: str = ""
    ptpTime: str = ""
    followUpAt: str = ""
    campaignName: Optional[str] = None
    ptpPaymentMethod: str = ""


class SkipCustomerModel(BaseModel):
    customerId: str
    campaignId: Optional[str] = None
    reason: str = ""


class NoteCreateModel(BaseModel):
    campaignName: Optional[str] = None
    note: str


class NoteUpdateModel(BaseModel):
    note: str


class TagModel(BaseModel):
    campaignName: Optional[str] = None
    tagCode: str


class PtpCreateModel(BaseModel):
    customerId: str
    campaignName: Optional[str] = None
    dispositionId: Optional[str] = None
    promisedAmount: float
    promisedDate: str
    paymentMethod: str = ""
    notes: str = ""
    createFollowup: bool = True


class PtpUpdateModel(BaseModel):
    notes: Optional[str] = None
    paymentMethod: Optional[str] = None


class PtpStatusUpdateModel(BaseModel):
    version: int
    status: str
    paidAmount: Optional[float] = None
    notes: str = ""


class FollowUpCreateModel(BaseModel):
    customerId: str
    campaignName: Optional[str] = None
    type: str = "CALLBACK"
    scheduledAt: str
    reason: str = ""
    notes: str = ""


class FollowUpCompleteModel(BaseModel):
    version: int
    outcome: str
    notes: str = ""


class FollowUpRescheduleModel(BaseModel):
    version: int
    newScheduledAt: str
    reason: str = ""
    notes: str = ""


class FollowUpCancelModel(BaseModel):
    version: int
    reason: str = ""


class FollowUpReassignModel(BaseModel):
    version: int
    newAgentName: str
    reason: str = ""

class CustomerAssignModel(BaseModel):
    customerId: str
    agentName: str

class ClaimNextCustomerModel(BaseModel):
    agentName: str

class AdminCampaignUpdateModel(BaseModel):
    name: str
    type: str
    priority: str
    startDate: str = ""
    endDate: str = ""
    archived: bool = False

class AdminCustomerUpdateModel(BaseModel):
    campaignName: str
    customerId: str
    name: str
    phone: str = ""
    branch: str = ""
    sector: str = ""
    balance: str = ""
    outcome: str = ""
    status: str = ""

class AdminDispositionUpdateModel(BaseModel):
    id: str
    outcome: str
    status: str = ""
    amountRec: float = 0.0
    comments: str = ""
    businessStatus: str = ""
    ptpTime: str = ""

class DistributionModel(BaseModel):
    campaign: str
    selectedAgents: List[str] = []

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
    url: str = ""
    disbDate: str = ""
    loanCode: str = ""
    ddDays: str = ""
    accountStatus: str = ""
    bfcBlc: str = ""
    numberOfLoans: str = ""
    riskBand: str = ""
    incrementStatus: str = ""
    affordability: str = ""
    loanLimit: str = ""
    interest: str = ""
    totalDue: str = ""
    penalty: str = ""

    class Config:
        extra = "allow"


def can_allocate(role: str) -> bool:
    return text(role).lower() in {"admin", "ops manager", "team leader"}

def can_administer(role: str) -> bool:
    return text(role).lower() in {"admin", "ops manager"}


def upload_value(raw_data: Dict[str, Any], *headers: str) -> Any:
    normalized_headers = {"".join(character for character in header.lower() if character.isalnum()) for header in headers}
    for key, value in reversed(list(raw_data.items())):
        normalized_key = "".join(character for character in key.lower() if character.isalnum())
        if normalized_key in normalized_headers and text(value):
            return value
    return ""


def upload_row(customer: CustomerUploadModel, campaign_id: str) -> Dict[str, Any]:
    raw_data = customer.model_dump() if hasattr(customer, "model_dump") else customer.dict()
    name = upload_value(raw_data, "name", "customer", "customer_name") or customer.name
    phone = upload_value(raw_data, "phone", "mobile no", "mobile_no", "mobile number") or customer.phone
    branch = upload_value(raw_data, "branch", "station", "stations") or customer.branch or customer.station or customer.stations
    sector = upload_value(raw_data, "sector") or customer.sector
    balance = upload_value(raw_data, "balance", "balance today") or customer.balance
    return {"campaign_id": campaign_id, "customer_id": str(customer.id), "name": text(name), "phone": text(phone), "branch": text(branch), "sector": text(sector), "balance": optional_number(balance), "due_date": optional_date(upload_value(raw_data, "due_date", "due date") or customer.dueDate), "pair": text(upload_value(raw_data, "pair") or customer.pair), "disb_amount": optional_number(upload_value(raw_data, "disb_amount", "disb amount") or customer.disbAmount), "total_paid": optional_number(upload_value(raw_data, "total_paid", "total paid") or customer.totalPaid), "source_url": text(upload_value(raw_data, "url", "shujaa_url", "merlin_url") or customer.url), "disb_date": text(upload_value(raw_data, "disb_date", "disb date", "loan_date") or customer.disbDate), "loan_code": text(upload_value(raw_data, "loan_code") or customer.loanCode), "dd_days": optional_integer(upload_value(raw_data, "dd_days", "dd days") or customer.ddDays), "account_status": text(upload_value(raw_data, "account_status", "status") or customer.accountStatus), "bfc_blc": text(upload_value(raw_data, "bfc_blc", "bfc/blc") or customer.bfcBlc), "number_of_loans": optional_integer(upload_value(raw_data, "number_of_loans", "no of loans", "loan_num") or customer.numberOfLoans), "risk_band": text(upload_value(raw_data, "risk_band", "risk band") or customer.riskBand), "increment_status": text(upload_value(raw_data, "increment_status", "increment") or customer.incrementStatus), "affordability": optional_number(upload_value(raw_data, "affordability") or customer.affordability), "loan_limit": optional_number(upload_value(raw_data, "loan_limit", "loan limit") or customer.loanLimit), "interest": optional_number(upload_value(raw_data, "interest") or customer.interest), "total_due": optional_number(upload_value(raw_data, "total_due", "total due") or customer.totalDue), "penalty": optional_number(upload_value(raw_data, "penalty") or customer.penalty), "feedback": text(raw_data.get("feedback")), "days_inactive": optional_integer(upload_value(raw_data, "days_inactive", "days_to_s", "days_since") or raw_data.get("daysInactive")), "days_dormant": optional_integer(upload_value(raw_data, "days_dormant", "days_dorm") or raw_data.get("daysDormant")), "loyalty": text(upload_value(raw_data, "loyalty") or raw_data.get("loyalty")), "last_loan_amount": optional_number(upload_value(raw_data, "last_loan_amount", "lastloan amount") or raw_data.get("lastLoanAmount")), "source_data": raw_data}


@app.get("/api")
def read_root():
    return {"message": "FastAPI Server is running successfully on Supabase!"}

@app.get("/")
def serve_frontend_root():
    # Instantly redirect visitors from the root URL to the login page
    return RedirectResponse(url="/login")


@app.get("/health")
@app.get("/api/health")
def health_check():
    configured = bool(os.getenv("SUPABASE_URL", "").strip() and os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip())
    return {"status": "ok" if configured else "configuration_required", "message": "Call Center API is running", "supabaseConfigured": configured}


@app.post("/api/login")
def login(creds: LoginModel):
    # This endpoint verifies the JWT token explicitly since it's the gateway
    try:
        idinfo = id_token.verify_oauth2_token(creds.token, google_requests.Request(), GOOGLE_CLIENT_ID)
        email = idinfo.get("email", "").lower()
    except ValueError:
        raise HTTPException(401, "Invalid Google authentication token.")

    if not email.endswith("@4g-capital.com"):
        raise HTTPException(403, "Only corporate @4g-capital.com accounts are permitted.")
    
    try:
        result = get_supabase().table("agents").select("*").eq("email", email).limit(1).execute()
    except Exception as error:
        raise db_error(error)
        
    if not result.data:
        raise HTTPException(403, "ACCESS DENIED: Your email is not registered in the active users database.")
        
    agent = result.data[0]
    if text(agent["status"]).lower() == "inactive":
        raise HTTPException(403, "ACCOUNT SUSPENDED: Your access has been revoked.")
        
    return {"success": True, "email": agent["email"], "role": agent["role"], "name": agent["name"]}


@app.get("/agents")
@app.get("/api/agents")
def get_agents(current_user: dict = Depends(get_current_user)):
    try:
        return [agent_response(row) for row in get_supabase().table("agents").select("*,performance:control_agent_performance(calls_made,connected,conversion)").order("name").execute().data]
    except Exception as error:
        raise db_error(error)


@app.post("/api/users/add")
def add_user(user: UserCreateModel, current_user: dict = Depends(get_current_user)):
    if not can_allocate(current_user["role"]):
        raise HTTPException(403, "Only managers can create users")
    if text(current_user["role"]).lower() == "team leader" and text(user.role).lower() != "control agent":
        raise HTTPException(403, "Team Leaders may only create Control Agents")
    try:
        get_supabase().table("agents").insert({"name": text(user.name), "email": text(user.email).lower(), "role": text(user.role), "status": text(user.status)}).execute()
    except Exception as error:
        if "duplicate" in str(error).lower():
            raise HTTPException(400, "A user with this email already exists.")
        raise db_error(error)
    return {"status": "success", "message": f"User {user.name} created successfully"}


@app.post("/api/users/edit")
def edit_user(user: UserEditModel, current_user: dict = Depends(get_current_user)):
    if not can_administer(current_user["role"]):
        raise HTTPException(403, "Only Admin or Ops Manager can edit users")
    try:
        result = get_supabase().table("agents").update({"name": text(user.name), "role": text(user.role), "status": text(user.status)}).eq("email", text(user.email).lower()).execute()
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "User not found in database.")
    return {"success": True}


@app.post("/api/users/delete")
def delete_user(user: UserDeleteModel, current_user: dict = Depends(get_current_user)):
    if not can_administer(current_user["role"]):
        raise HTTPException(403, "Only Admin or Ops Manager can delete users")
    try:
        result = get_supabase().table("agents").delete().eq("email", text(user.email).lower()).execute()
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "User not found in database.")
    return {"success": True}


@app.put("/agents/status")
@app.put("/api/agents/status")
def update_agent_status(name: str = Body(...), status: str = Body(...), current_user: dict = Depends(get_current_user)):
    try:
        result = get_supabase().table("agents").update({"status": text(status)}).eq("name", text(name)).execute()
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Agent not found in database")
    return {"status": "success"}


@app.get("/api/campaigns")
def get_campaigns(fresh: bool = False, current_user: dict = Depends(get_current_user)):
    try:
        rows = get_supabase().table("campaign_summary").select("*").order("date_added", desc=True).execute().data
        # Fixed: using row.get("archived_at") instead of row["archived_at"]
        return [{"name": row["name"], "type": row["type"], "priority": row["priority"], "startDate": row["start_date"] or "", "endDate": row["end_date"] or "", "dateAdded": row["date_added"] or "", "archivedAt": row.get("archived_at") or "", "accountCount": row["account_count"]} for row in rows]
    except Exception as error:
        raise db_error(error)


@app.post("/api/campaigns")
def create_campaign(name: str = Body(...), type: str = Body(...), priority: str = Body(...), startDate: str = Body(""), endDate: str = Body(""), customers: List[CustomerUploadModel] = Body(...), chunkIndex: int = Body(0), current_user: dict = Depends(get_current_user)):
    if not can_allocate(current_user["role"]):
        raise HTTPException(403, "Only managers can upload campaigns")
    if len(customers) > 4000:
        raise HTTPException(413, "Each upload request may contain at most 4,000 accounts")
    try:
        db = get_supabase()
        campaign = with_retry(lambda: db.table("campaigns").upsert({"name": text(name), "type": text(type), "priority": text(priority), "start_date": optional_date(startDate), "end_date": optional_date(endDate)}, on_conflict="name").execute()).data[0]
        rows = [upload_row(customer, campaign["id"]) for customer in customers]
        if rows:
            with_retry(lambda: db.table("customers").upsert(rows, on_conflict="campaign_id,customer_id").execute())
    except Exception as error:
        raise db_error(error)
    return {"status": "success", "imported": len(customers), "campaignSheet": text(name)}


@app.get("/api/admin/dispositions")
def get_admin_dispositions(campaignName: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    if not can_administer(current_user["role"]):
        raise HTTPException(403, "Only Admin or Ops Manager can view disposition history")
    try:
        query = get_supabase().table("dispositions").select("id,customer_id,outcome,status,amount_rec,comments,business_status,ptp_time,created_at,campaign:campaigns(name),agent:agents(name)").order("created_at", desc=True).limit(200)
        if campaignName:
            campaigns = get_supabase().table("campaigns").select("id").eq("name", text(campaignName)).limit(1).execute().data
            if not campaigns:
                return []
            query = query.eq("campaign_id", campaigns[0]["id"])
        return query.execute().data
    except Exception as error:
        raise db_error(error)


@app.patch("/api/admin/campaigns/{campaign_name}")
def update_admin_campaign(campaign_name: str, campaign: AdminCampaignUpdateModel, current_user: dict = Depends(get_current_user)):
    if not can_administer(current_user["role"]):
        raise HTTPException(403, "Only Admin or Ops Manager can modify campaigns")
    try:
        result = get_supabase().table("campaigns").update({"name": text(campaign.name), "type": text(campaign.type), "priority": text(campaign.priority), "start_date": optional_date(campaign.startDate), "end_date": optional_date(campaign.endDate), "archived_at": datetime.now(timezone.utc).isoformat() if campaign.archived else None}).eq("name", text(campaign_name)).execute()
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Campaign not found")
    return {"status": "success"}


@app.patch("/api/admin/customers")
def update_admin_customer(customer: AdminCustomerUpdateModel, current_user: dict = Depends(get_current_user)):
    if not can_administer(current_user["role"]):
        raise HTTPException(403, "Only Admin or Ops Manager can modify customers")
    try:
        db = get_supabase()
        campaigns = db.table("campaigns").select("id").eq("name", text(customer.campaignName)).limit(1).execute().data
        if not campaigns:
            raise HTTPException(404, "Campaign not found")
        result = db.table("customers").update({"name": text(customer.name), "phone": text(customer.phone), "branch": text(customer.branch), "sector": text(customer.sector), "balance": optional_number(customer.balance), "outcome": text(customer.outcome), "status": text(customer.status)}).eq("campaign_id", campaigns[0]["id"]).eq("customer_id", text(customer.customerId)).execute()
    except HTTPException:
        raise
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Customer not found")
    return {"status": "success"}


@app.patch("/api/admin/dispositions/{disposition_id}")
def update_admin_disposition(disposition_id: str, disposition: AdminDispositionUpdateModel, current_user: dict = Depends(get_current_user)):
    if not can_administer(current_user["role"]):
        raise HTTPException(403, "Only Admin or Ops Manager can modify dispositions")
    try:
        result = get_supabase().table("dispositions").update({"outcome": text(disposition.outcome), "status": text(disposition.status), "amount_rec": disposition.amountRec, "comments": text(disposition.comments), "business_status": text(disposition.businessStatus), "ptp_time": text(disposition.ptpTime)}).eq("id", disposition_id).execute()
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Disposition not found")
    return {"status": "success"}


CUSTOMER_SELECT = "campaign_id,customer_id,name,phone,branch,sector,balance,due_date,pair,disb_amount,total_paid,worked,outcome,status,business_status,ptp_amount,ptp_time,feedback,days_inactive,days_dormant,loyalty,last_loan_amount,source_data,updated_at,campaign:campaigns(name),assigned_agent:agents!customers_assigned_agent_id_fkey(name)"


@app.get("/customers")
@app.get("/api/customers")
def get_customers(
    agentName: Optional[str] = None, 
    campaignName: Optional[str] = None, 
    pending: bool = False, 
    offset: int = 0, 
    limit: int = 1000, 
    current_user: dict = Depends(get_current_user)
):
    if offset < 0 or limit < 1 or limit > 1000:
        raise HTTPException(400, "offset must be >= 0 and limit must be between 1 and 1000")
        
    try:
        db = get_supabase()
        
        # 1. Resolve Agent ID
        agent_id = None
        if agentName:
            agents = db.table("agents").select("id").eq("name", str(agentName).strip()).limit(1).execute().data
            if not agents:
                return {"items": [], "offset": offset, "limit": limit, "total": 0, "hasMore": False}
            agent_id = agents[0]["id"]
            
        # 2. Resolve Campaign ID
        campaign_id = None
        if campaignName:
            campaigns = db.table("campaigns").select("id").eq("name", str(campaignName).strip()).limit(1).execute().data
            if not campaigns:
                return {"items": [], "offset": offset, "limit": limit, "total": 0, "hasMore": False}
            campaign_id = campaigns[0]["id"]
            
        # 3. Construct Query
        query = db.table("customers").select(CUSTOMER_SELECT, count="exact")
        
        if agent_id:
            query = query.eq("assigned_agent_id", agent_id)
        if campaign_id:
            query = query.eq("campaign_id", campaign_id)
            
        # --- THE FIX: Only filter by 'worked' if we are specifically asking for pending accounts! ---
        # (Otherwise, fetch everything so Analytics and PTP tabs can read the data)
        if pending:
            query = query.eq("worked", True)
            
        result = query.order("updated_at", desc=True).range(offset, offset + limit - 1).execute()
        
    except Exception as error:
        raise db_error(error)

    # 4. Safely filter pending callbacks
    rows = [
        row for row in (result.data or []) 
        if not pending or str(row.get("outcome") or "").strip().lower() not in {"", "answered"}
    ]
    
    total = len(rows) if pending else (result.count or 0)
    
    return {
        "items": [customer_response(row) for row in rows], 
        "offset": offset, 
        "limit": limit, 
        "total": total, 
        "hasMore": (offset + len(rows)) < total
    }

@app.get("/api/ptps")
def get_agent_ptps(agentName: str, campaignName: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    try:
        db = get_supabase()
        agents = db.table("agents").select("id").eq("name", text(agentName)).limit(1).execute().data
        if not agents:
            return []
        query = db.table("customers").select(CUSTOMER_SELECT).eq("assigned_agent_id", agents[0]["id"]).eq("status", "Promise to Pay (PTP)")
        if campaignName:
            campaigns = db.table("campaigns").select("id").eq("name", text(campaignName)).limit(1).execute().data
            if not campaigns:
                return []
            query = query.eq("campaign_id", campaigns[0]["id"])
        return [customer_response(row) for row in query.order("ptp_time").execute().data]
    except Exception as error:
        raise db_error(error)


@app.post("/api/assign")
def assign_customer(assignment: CustomerAssignModel, current_user: dict = Depends(get_current_user)):
    if not can_allocate(current_user["role"]):
        raise HTTPException(403, "Only managers can assign accounts")
    try:
        result = with_retry(lambda: get_supabase().rpc("assign_customer", {"p_customer_id": assignment.customerId, "p_agent_name": assignment.agentName}).execute())
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Customer or agent not found")
    return {"status": "success", "customerId": assignment.customerId, "agentName": assignment.agentName}


@app.post("/api/claim-next-customer")
def claim_next_customer(claim: ClaimNextCustomerModel, current_user: dict = Depends(get_current_user)):
    if text(current_user["role"]) != "Admin":
        raise HTTPException(403, "Only Admin users can take accounts")
    try:
        db = get_supabase()
        agents = db.table("agents").select("id").eq("name", text(claim.agentName)).limit(1).execute().data
        if not agents:
            raise HTTPException(404, "Agent not found")
        available = db.table("customers").select(CUSTOMER_SELECT).is_("assigned_agent_id", "null").eq("worked", False).order("created_at").limit(1).execute().data
        if not available:
            raise HTTPException(404, "No unassigned accounts are available")
        customer = available[0]
        result = db.table("customers").update({"assigned_agent_id": agents[0]["id"]}).eq("campaign_id", customer["campaign_id"]).eq("customer_id", customer["customer_id"]).is_("assigned_agent_id", "null").execute()
        if not result.data:
            raise HTTPException(409, "That account was just assigned. Try again.")
        customer["assigned_agent"] = {"name": text(claim.agentName)}
        return customer_response(customer)
    except HTTPException:
        raise
    except Exception as error:
        raise db_error(error)


@app.post("/api/distribute")
def distribute_customers(distribution: DistributionModel, current_user: dict = Depends(get_current_user)):
    if not can_allocate(current_user["role"]):
        raise HTTPException(403, "Only managers can assign accounts")
    if not distribution.selectedAgents:
        raise HTTPException(400, "Select at least one active agent")
    try:
        assigned = int(with_retry(lambda: get_supabase().rpc("distribute_campaign", {"p_campaign_name": distribution.campaign, "p_agent_names": distribution.selectedAgents}).execute()).data or 0)
    except Exception as error:
        raise db_error(error)
    return {"status": "success", "assignedCount": assigned} if assigned else {"status": "info", "message": "No unassigned customers remaining"}


# --- INTELLIGENT NEXT CUSTOMER QUEUE ---
# Only the columns the scoring engine and customer_response() need; kept separate from
# CUSTOMER_SELECT so other endpoints are unaffected by the new queue columns.
NEXT_CUSTOMER_SELECT = (
    "campaign_id,customer_id,name,phone,branch,sector,balance,due_date,pair,disb_amount,total_paid,"
    "assigned_agent_id,worked,outcome,status,business_status,ptp_amount,ptp_time,feedback,days_inactive,"
    "days_dormant,loyalty,last_loan_amount,source_data,updated_at,attempts,last_contact_at,follow_up_at,"
    "locked_by,lock_expires_at,campaign:campaigns(name,priority),assigned_agent:agents!customers_assigned_agent_id_fkey(name)"
)


@app.get("/api/agent/next-customer")
def get_next_customer(campaignName: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    """Reserves and returns the highest-priority eligible customer in the caller's own queue.

    Authorization: the agent identity comes only from the authenticated session (get_current_user);
    there is no agent_id request parameter, so one agent can never fetch another agent's customer.
    """
    if text(current_user["role"]).lower() != "control agent":
        raise HTTPException(403, "Only Control Agents have a personal Next Customer queue.")

    db = get_supabase()
    agent_id = current_user["id"]
    try:
        with_retry(lambda: db.rpc("release_expired_locks", {}).execute())
        with_retry(lambda: db.rpc("refresh_followup_states", {}).execute())

        query = db.table("customers").select(NEXT_CUSTOMER_SELECT).eq("assigned_agent_id", agent_id).eq("worked", False)
        campaign_id = None
        if campaignName:
            campaigns = db.table("campaigns").select("id").eq("name", text(campaignName)).limit(1).execute().data
            if not campaigns:
                return {"customer": None, "message": "No customers available"}
            campaign_id = campaigns[0]["id"]
            query = query.eq("campaign_id", campaign_id)

        # Server-side filter narrows to this agent's pending queue only (uses customers_next_queue_idx);
        # the full customer table is never scanned or shipped to the browser.
        candidates = query.limit(2000).execute().data or []

        # Bounded to this agent's own open items (indexed by agent_id), so joining them onto the
        # candidate set in Python is cheap and keeps scoring authoritative on the backend.
        open_followups = db.table("follow_ups").select("campaign_id,customer_id,status,scheduled_at").eq("agent_id", agent_id).in_("status", list(followups_module.OPEN_FOLLOWUP_STATUSES)).limit(2000).execute().data or []
        open_ptps = db.table("promise_to_pay").select("campaign_id,customer_id,status,promised_date").eq("agent_id", agent_id).in_("status", list(followups_module.OPEN_PTP_STATUSES)).limit(2000).execute().data or []
    except Exception as error:
        raise db_error(error)

    followups_by_key = {(row["campaign_id"], row["customer_id"]): row for row in open_followups}
    ptps_by_key = {(row["campaign_id"], row["customer_id"]): row for row in open_ptps}

    weights = get_weights(db)
    ranked = rank_candidates(candidates, weights, followups_by_key, ptps_by_key)

    for index, (score, reasons, row) in enumerate(ranked):
        try:
            locked = with_retry(lambda: db.rpc("reserve_customer_lock", {
                "p_agent_id": agent_id,
                "p_campaign_id": row["campaign_id"],
                "p_customer_id": row["customer_id"],
                "p_lock_minutes": int(weights.get("lock_minutes", 12)),
            }).execute()).data
        except Exception as error:
            raise db_error(error)
        if locked:
            return {
                "customer": customer_response(row),
                "campaign": {"name": (row.get("campaign") or {}).get("name", ""), "priority": (row.get("campaign") or {}).get("priority", "")},
                "priorityScore": score,
                "priorityReasons": reasons,
                "queuePosition": index + 1,
            }
        # Another request/tab reserved this candidate a moment ago; try the next-best one.

    return {"customer": None, "message": "No customers available"}


@app.post("/api/agent/skip-customer")
def skip_customer_endpoint(payload: SkipCustomerModel, current_user: dict = Depends(get_current_user)):
    if text(current_user["role"]).lower() != "control agent":
        raise HTTPException(403, "Only Control Agents can skip customers in their queue.")

    db = get_supabase()
    agent_id = current_user["id"]
    campaign_id = payload.campaignId
    if not campaign_id:
        rows = db.table("customers").select("campaign_id").eq("customer_id", text(payload.customerId)).eq("assigned_agent_id", agent_id).limit(1).execute().data
        if not rows:
            raise HTTPException(404, "Customer not found in your queue")
        campaign_id = rows[0]["campaign_id"]

    try:
        result = with_retry(lambda: db.rpc("skip_customer", {
            "p_agent_id": agent_id,
            "p_campaign_id": campaign_id,
            "p_customer_id": text(payload.customerId),
            "p_reason": text(payload.reason),
        }).execute())
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Customer not found in your queue")
    return {"status": "success"}


@app.get("/api/agent/queue-summary")
def get_queue_summary_endpoint(current_user: dict = Depends(get_current_user)):
    if text(current_user["role"]).lower() != "control agent":
        raise HTTPException(403, "Only Control Agents have a personal queue.")
    try:
        result = with_retry(lambda: get_supabase().rpc("get_queue_summary", {"p_agent_id": current_user["id"]}).execute())
    except Exception as error:
        raise db_error(error)
    row = (result.data or [{}])[0] if isinstance(result.data, list) else (result.data or {})
    return {
        "highPriority": row.get("high_priority", 0),
        "followUps": row.get("follow_ups", 0),
        "ptpCustomers": row.get("ptp_customers", 0),
        "newCustomers": row.get("new_customers", 0),
        "total": row.get("total", 0),
    }


# --- CUSTOMER 360 ---
ACCOUNT_SELECT = (
    "campaign_id,customer_id,name,phone,branch,sector,balance,due_date,pair,disb_amount,total_paid,"
    "assigned_agent_id,worked,outcome,status,business_status,ptp_amount,ptp_time,feedback,days_inactive,"
    "days_dormant,loyalty,last_loan_amount,source_data,updated_at,attempts,last_contact_at,follow_up_at,"
    "source_url,disb_date,loan_code,dd_days,account_status,bfc_blc,number_of_loans,risk_band,increment_status,"
    "affordability,loan_limit,interest,total_due,penalty,"
    "campaign:campaigns(name,priority,type),assigned_agent:agents!customers_assigned_agent_id_fkey(name)"
)
DISPOSITION_SELECT = "id,customer_id,campaign_id,agent_id,outcome,status,amount_rec,comments,business_status,ptp_time,follow_up_at,created_at,agent:agents(name)"
NOTE_SELECT = "id,campaign_id,customer_id,agent_id,note,created_at,updated_at,agent:agents(name)"
TAG_SELECT = "tag_code,added_by,added_at,definition:customer_tag_definitions(label),agent:agents(name)"
FOLLOWUP_SELECT = (
    "id,campaign_id,customer_id,agent_id,created_by,type,reason,scheduled_at,status,priority,notes,"
    "completed_at,completed_by,completed_outcome,previous_agent,reassigned_by,reassigned_at,reassignment_reason,"
    "rescheduled_from,ptp_id,version,created_at,updated_at,"
    "campaign:campaigns(name),agent:agents!follow_ups_agent_id_fkey(name)"
)
PTP_SELECT = (
    "id,campaign_id,customer_id,agent_id,disposition_id,follow_up_id,promised_amount,promised_date,"
    "payment_method,status,paid_amount,remaining_amount,notes,version,created_at,updated_at,"
    "campaign:campaigns(name),agent:agents!promise_to_pay_agent_id_fkey(name)"
)


def can_view_customer(current_user: Dict[str, Any], customer_row: Dict[str, Any]) -> bool:
    role = text(current_user.get("role")).lower()
    if role in {"admin", "ops manager", "team leader"}:
        return True
    if role == "control agent":
        return customer_row.get("assigned_agent_id") == current_user.get("id")
    return False


def resolve_customer_360(db, customer_id: str, campaign_name: Optional[str]) -> Optional[Dict[str, Any]]:
    query = db.table("customers").select(ACCOUNT_SELECT).eq("customer_id", text(customer_id))
    if campaign_name:
        campaigns = db.table("campaigns").select("id").eq("name", text(campaign_name)).limit(1).execute().data
        if not campaigns:
            return None
        query = query.eq("campaign_id", campaigns[0]["id"])
    rows = query.order("created_at").limit(1).execute().data
    return rows[0] if rows else None


def account_response(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "accountNumber": row["customer_id"], "loanCode": row.get("loan_code") or "", "product": (row.get("campaign") or {}).get("type", ""),
        "disbursementDate": row.get("disb_date") or "", "dueDate": row.get("due_date") or "", "originalAmount": row.get("disb_amount"),
        "outstandingAmount": row.get("balance"), "paidAmount": row.get("total_paid"), "daysInArrears": row.get("dd_days"),
        "riskBand": row.get("risk_band") or "", "branch": row.get("branch") or "", "accountStatus": row.get("account_status") or "",
        "bfcBlc": row.get("bfc_blc") or "", "numberOfLoans": row.get("number_of_loans"), "incrementStatus": row.get("increment_status") or "",
        "affordability": row.get("affordability"), "loanLimit": row.get("loan_limit"), "interest": row.get("interest"),
        "totalDue": row.get("total_due"), "penalty": row.get("penalty"), "sourceUrl": row.get("source_url") or "",
    }


def note_response(row: Dict[str, Any], current_user: Dict[str, Any]) -> Dict[str, Any]:
    can_edit = row.get("agent_id") == current_user.get("id") or can_administer(current_user.get("role", ""))
    return {
        "id": row["id"], "note": row["note"], "agent": (row.get("agent") or {}).get("name", ""),
        "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"), "canEdit": can_edit,
    }


def tag_response(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": row["tag_code"], "label": (row.get("definition") or {}).get("label", row["tag_code"]),
        "addedBy": (row.get("agent") or {}).get("name", ""), "addedAt": row.get("added_at"),
    }


def log_audit_event(db, agent_id: Optional[str], campaign_id: str, customer_id: str, action: str, reason: str = "") -> None:
    try:
        db.table("queue_audit_log").insert({"agent_id": agent_id, "campaign_id": campaign_id, "customer_id": customer_id, "action": action, "reason": reason}).execute()
    except Exception as error:
        print(f"[audit_log_error] {error}")


@app.get("/api/customers/{customer_id}/360")
def get_customer_360(customer_id: str, campaignName: Optional[str] = None, page: int = 1, pageSize: int = 20, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        customer_row = resolve_customer_360(db, customer_id, campaignName)
    except Exception as error:
        raise db_error(error)
    if not customer_row:
        raise HTTPException(404, "Customer not found")
    if not can_view_customer(current_user, customer_row):
        raise HTTPException(403, "You are not authorized to view this customer")

    try:
        dispositions = db.table("dispositions").select(DISPOSITION_SELECT).eq("campaign_id", customer_row["campaign_id"]).eq("customer_id", customer_row["customer_id"]).order("created_at", desc=True).limit(500).execute().data or []
        notes = db.table("customer_notes").select(NOTE_SELECT).eq("campaign_id", customer_row["campaign_id"]).eq("customer_id", customer_row["customer_id"]).order("created_at", desc=True).limit(200).execute().data or []
        tags = db.table("customer_tags").select(TAG_SELECT).eq("campaign_id", customer_row["campaign_id"]).eq("customer_id", customer_row["customer_id"]).execute().data or []
        audit_events = db.table("queue_audit_log").select("action,reason,created_at,agent:agents(name)").eq("campaign_id", customer_row["campaign_id"]).eq("customer_id", customer_row["customer_id"]).order("created_at", desc=True).limit(200).execute().data or []
        with_retry(lambda: db.rpc("refresh_followup_states", {}).execute())
        followup_rows = db.table("follow_ups").select(FOLLOWUP_SELECT).eq("campaign_id", customer_row["campaign_id"]).eq("customer_id", customer_row["customer_id"]).order("scheduled_at", desc=True).limit(200).execute().data or []
        ptp_rows = db.table("promise_to_pay").select(PTP_SELECT).eq("campaign_id", customer_row["campaign_id"]).eq("customer_id", customer_row["customer_id"]).order("created_at", desc=True).limit(200).execute().data or []
    except Exception as error:
        raise db_error(error)

    log_audit_event(db, current_user.get("id"), customer_row["campaign_id"], customer_row["customer_id"], "CUSTOMER_VIEWED")

    timeline = customer360.build_timeline(dispositions, notes, audit_events)
    timeline_page, timeline_total, timeline_has_more = customer360.paginate(timeline, page, pageSize)
    contact_page, contact_total, contact_has_more = customer360.paginate(customer360.build_contact_history(dispositions), page, pageSize)

    current_followup_row = customer360.current_followup(followup_rows)
    current_ptp_row = customer360.current_ptp(ptp_rows)

    return {
        "customer": customer_response(customer_row),
        "account": account_response(customer_row),
        "campaigns": [{"name": (customer_row.get("campaign") or {}).get("name", ""), "priority": (customer_row.get("campaign") or {}).get("priority", ""), "type": (customer_row.get("campaign") or {}).get("type", "")}],
        "summary": customer360.build_summary(customer_row, dispositions),
        "contactability": customer360.build_contactability(dispositions),
        "contact_history": {"items": contact_page, "page": page, "pageSize": pageSize, "total": contact_total, "hasMore": contact_has_more},
        "dispositions": customer360.build_contact_history(dispositions)[:pageSize],
        "ptp_history": customer360.build_ptp_history_v2(ptp_rows) if ptp_rows else customer360.build_ptp_history(dispositions, customer_row),
        "followups": customer360.build_followup_history_v2(followup_rows) if followup_rows else customer360.build_followups(dispositions),
        "currentFollowUp": followups_module.followup_response(current_followup_row) if current_followup_row else None,
        "currentPtp": followups_module.ptp_response(current_ptp_row) if current_ptp_row else None,
        "notes": [note_response(row, current_user) for row in notes],
        "tags": [tag_response(row) for row in tags],
        "timeline": {"items": timeline_page, "page": page, "pageSize": pageSize, "total": timeline_total, "hasMore": timeline_has_more},
    }


@app.get("/api/customers/{customer_id}/timeline")
def get_customer_timeline(customer_id: str, campaignName: Optional[str] = None, page: int = 1, pageSize: int = 50, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        customer_row = resolve_customer_360(db, customer_id, campaignName)
    except Exception as error:
        raise db_error(error)
    if not customer_row:
        raise HTTPException(404, "Customer not found")
    if not can_view_customer(current_user, customer_row):
        raise HTTPException(403, "You are not authorized to view this customer")
    try:
        dispositions = db.table("dispositions").select(DISPOSITION_SELECT).eq("campaign_id", customer_row["campaign_id"]).eq("customer_id", customer_row["customer_id"]).order("created_at", desc=True).limit(500).execute().data or []
        notes = db.table("customer_notes").select(NOTE_SELECT).eq("campaign_id", customer_row["campaign_id"]).eq("customer_id", customer_row["customer_id"]).order("created_at", desc=True).limit(200).execute().data or []
        audit_events = db.table("queue_audit_log").select("action,reason,created_at,agent:agents(name)").eq("campaign_id", customer_row["campaign_id"]).eq("customer_id", customer_row["customer_id"]).order("created_at", desc=True).limit(200).execute().data or []
    except Exception as error:
        raise db_error(error)
    timeline = customer360.build_timeline(dispositions, notes, audit_events)
    items, total, has_more = customer360.paginate(timeline, page, pageSize)
    return {"items": items, "page": page, "pageSize": pageSize, "total": total, "hasMore": has_more}


@app.post("/api/customers/{customer_id}/notes")
def add_customer_note(customer_id: str, payload: NoteCreateModel, current_user: dict = Depends(get_current_user)):
    if not text(payload.note).strip():
        raise HTTPException(400, "Note cannot be empty")
    db = get_supabase()
    try:
        customer_row = resolve_customer_360(db, customer_id, payload.campaignName)
    except Exception as error:
        raise db_error(error)
    if not customer_row:
        raise HTTPException(404, "Customer not found")
    if not can_view_customer(current_user, customer_row):
        raise HTTPException(403, "You are not authorized to add notes for this customer")
    try:
        inserted = db.table("customer_notes").insert({
            "campaign_id": customer_row["campaign_id"], "customer_id": customer_row["customer_id"],
            "agent_id": current_user["id"], "note": text(payload.note),
        }).execute().data
    except Exception as error:
        raise db_error(error)
    log_audit_event(db, current_user.get("id"), customer_row["campaign_id"], customer_row["customer_id"], "CUSTOMER_NOTE_ADDED")
    return note_response({**inserted[0], "agent": {"name": current_user.get("name", "")}}, current_user)


@app.patch("/api/customers/notes/{note_id}")
def edit_customer_note(note_id: str, payload: NoteUpdateModel, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        existing = db.table("customer_notes").select("id,agent_id,campaign_id,customer_id").eq("id", note_id).limit(1).execute().data
    except Exception as error:
        raise db_error(error)
    if not existing:
        raise HTTPException(404, "Note not found")
    note_row = existing[0]
    if note_row.get("agent_id") != current_user.get("id") and not can_administer(current_user.get("role", "")):
        raise HTTPException(403, "Only the author or an administrator can edit this note")
    try:
        result = db.table("customer_notes").update({"note": text(payload.note)}).eq("id", note_id).execute()
    except Exception as error:
        raise db_error(error)
    return {"status": "success", "data": result.data}


@app.get("/api/tags")
def list_tag_definitions(current_user: dict = Depends(get_current_user)):
    try:
        return get_supabase().table("customer_tag_definitions").select("code,label").order("label").execute().data
    except Exception as error:
        raise db_error(error)


@app.post("/api/customers/{customer_id}/tags")
def add_customer_tag(customer_id: str, payload: TagModel, current_user: dict = Depends(get_current_user)):
    if not can_allocate(current_user["role"]):
        raise HTTPException(403, "Only managers can tag customers")
    db = get_supabase()
    try:
        customer_row = resolve_customer_360(db, customer_id, payload.campaignName)
    except Exception as error:
        raise db_error(error)
    if not customer_row:
        raise HTTPException(404, "Customer not found")
    try:
        db.table("customer_tags").upsert({
            "campaign_id": customer_row["campaign_id"], "customer_id": customer_row["customer_id"],
            "tag_code": text(payload.tagCode), "added_by": current_user["id"],
        }, on_conflict="campaign_id,customer_id,tag_code").execute()
    except Exception as error:
        raise db_error(error)
    log_audit_event(db, current_user.get("id"), customer_row["campaign_id"], customer_row["customer_id"], "CUSTOMER_TAG_CHANGED", f"added:{payload.tagCode}")
    return {"status": "success"}


@app.delete("/api/customers/{customer_id}/tags/{tag_code}")
def remove_customer_tag(customer_id: str, tag_code: str, campaignName: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    if not can_allocate(current_user["role"]):
        raise HTTPException(403, "Only managers can tag customers")
    db = get_supabase()
    try:
        customer_row = resolve_customer_360(db, customer_id, campaignName)
    except Exception as error:
        raise db_error(error)
    if not customer_row:
        raise HTTPException(404, "Customer not found")
    try:
        db.table("customer_tags").delete().eq("campaign_id", customer_row["campaign_id"]).eq("customer_id", customer_row["customer_id"]).eq("tag_code", text(tag_code)).execute()
    except Exception as error:
        raise db_error(error)
    log_audit_event(db, current_user.get("id"), customer_row["campaign_id"], customer_row["customer_id"], "CUSTOMER_TAG_CHANGED", f"removed:{tag_code}")
    return {"status": "success"}


# --- FOLLOW-UPS ---
def can_view_followup(current_user: Dict[str, Any], row: Dict[str, Any]) -> bool:
    if can_allocate(current_user.get("role", "")):
        return True
    return row.get("agent_id") == current_user.get("id")


def resolve_agent_id_by_name(db, name: str) -> Optional[str]:
    rows = db.table("agents").select("id").eq("name", text(name)).limit(1).execute().data
    return rows[0]["id"] if rows else None


def _followup_list_query(db, current_user: Dict[str, Any], agent_name: Optional[str], campaign_name: Optional[str]):
    query = db.table("follow_ups").select(FOLLOWUP_SELECT)
    role = text(current_user.get("role", "")).lower()
    if role == "control agent":
        query = query.eq("agent_id", current_user["id"])
    elif agent_name:
        agent_id = resolve_agent_id_by_name(db, agent_name)
        query = query.eq("agent_id", agent_id or "00000000-0000-0000-0000-000000000000")
    if campaign_name:
        campaigns = db.table("campaigns").select("id").eq("name", text(campaign_name)).limit(1).execute().data
        query = query.eq("campaign_id", campaigns[0]["id"] if campaigns else "00000000-0000-0000-0000-000000000000")
    return query


@app.get("/api/followups")
def list_followups(scope: Optional[str] = None, agentName: Optional[str] = None, campaignName: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        with_retry(lambda: db.rpc("refresh_followup_states", {}).execute())
        rows = _followup_list_query(db, current_user, agentName, campaignName).order("scheduled_at").limit(1000).execute().data or []
    except Exception as error:
        raise db_error(error)
    buckets = followups_module.bucket_followups(rows)
    if scope and scope in buckets:
        return {"items": [followups_module.followup_response(row) for row in buckets[scope]]}
    return {key: [followups_module.followup_response(row) for row in items] for key, items in buckets.items()}


@app.get("/api/followups/today")
def list_followups_today(agentName: Optional[str] = None, campaignName: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    return list_followups(scope="today", agentName=agentName, campaignName=campaignName, current_user=current_user)


@app.get("/api/followups/overdue")
def list_followups_overdue(agentName: Optional[str] = None, campaignName: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    return list_followups(scope="overdue", agentName=agentName, campaignName=campaignName, current_user=current_user)


@app.get("/api/followups/summary")
def followups_summary(agentName: Optional[str] = None, campaignName: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        with_retry(lambda: db.rpc("refresh_followup_states", {}).execute())
        rows = _followup_list_query(db, current_user, agentName, campaignName).limit(1000).execute().data or []
    except Exception as error:
        raise db_error(error)
    return followups_module.followup_summary_counts(rows)


@app.post("/api/followups")
def create_followup(payload: FollowUpCreateModel, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        customer_row = resolve_customer_360(db, payload.customerId, payload.campaignName)
    except Exception as error:
        raise db_error(error)
    if not customer_row:
        raise HTTPException(404, "Customer not found")
    if not can_view_customer(current_user, customer_row):
        raise HTTPException(403, "You are not authorized to schedule a follow-up for this customer")
    scheduled_at = optional_datetime(payload.scheduledAt)
    if not scheduled_at:
        raise HTTPException(400, "A valid scheduledAt date/time is required")
    target_agent_id = customer_row.get("assigned_agent_id") or current_user["id"]
    try:
        result = with_retry(lambda: db.rpc("create_callback_followup", {
            "p_agent_id": target_agent_id, "p_campaign_id": customer_row["campaign_id"], "p_customer_id": customer_row["customer_id"],
            "p_scheduled_at": scheduled_at, "p_reason": text(payload.reason), "p_notes": text(payload.notes),
        }).execute())
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(400, "Could not create follow-up")
    return {"status": "success", "id": result.data}


@app.get("/api/followups/{followup_id}")
def get_followup(followup_id: str, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        rows = db.table("follow_ups").select(FOLLOWUP_SELECT).eq("id", followup_id).limit(1).execute().data
    except Exception as error:
        raise db_error(error)
    if not rows:
        raise HTTPException(404, "Follow-up not found")
    row = rows[0]
    if not can_view_followup(current_user, row):
        raise HTTPException(403, "You are not authorized to view this follow-up")
    try:
        history = db.table("follow_ups").select(FOLLOWUP_SELECT).eq("campaign_id", row["campaign_id"]).eq("customer_id", row["customer_id"]).order("scheduled_at", desc=True).limit(50).execute().data or []
        audit = db.table("queue_audit_log").select("action,reason,created_at,agent:agents(name)").eq("campaign_id", row["campaign_id"]).eq("customer_id", row["customer_id"]).like("action", "FOLLOWUP%").order("created_at", desc=True).limit(50).execute().data or []
    except Exception as error:
        raise db_error(error)
    return {"followup": followups_module.followup_response(row), "history": [followups_module.followup_response(item) for item in history], "auditLog": audit}


@app.post("/api/followups/{followup_id}/complete")
def complete_followup(followup_id: str, payload: FollowUpCompleteModel, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        rows = db.table("follow_ups").select(FOLLOWUP_SELECT).eq("id", followup_id).limit(1).execute().data
    except Exception as error:
        raise db_error(error)
    if not rows:
        raise HTTPException(404, "Follow-up not found")
    row = rows[0]
    if not can_view_followup(current_user, row):
        raise HTTPException(403, "You are not authorized to complete this follow-up")
    try:
        completed = with_retry(lambda: db.rpc("complete_follow_up", {
            "p_follow_up_id": followup_id, "p_agent_id": current_user["id"], "p_version": payload.version,
            "p_outcome": text(payload.outcome), "p_notes": text(payload.notes),
        }).execute()).data
    except Exception as error:
        raise db_error(error)
    if not completed:
        raise HTTPException(409, "This follow-up was already updated by someone else. Refresh and try again.")

    ptp_status_updated = None
    weights = get_weights(db)
    outcome_lower = text(payload.outcome).lower()
    if row.get("ptp_id") and weights.get("auto_confirm_ptp_outcomes", True):
        new_status = None
        if outcome_lower == "payment received":
            new_status = "FULFILLED"
        elif outcome_lower == "payment partially received":
            new_status = "PARTIALLY_PAID"
        elif outcome_lower == "promise broken":
            new_status = "BROKEN"
        if new_status:
            try:
                ptp_rows = db.table("promise_to_pay").select("id,version,promised_amount").eq("id", row["ptp_id"]).limit(1).execute().data
                if ptp_rows:
                    ptp_row = ptp_rows[0]
                    paid_amount = ptp_row["promised_amount"] if new_status == "FULFILLED" else None
                    with_retry(lambda: db.rpc("update_ptp_status", {
                        "p_ptp_id": row["ptp_id"], "p_agent_id": current_user["id"], "p_version": ptp_row["version"],
                        "p_status": new_status, "p_paid_amount": paid_amount, "p_notes": text(payload.notes),
                    }).execute())
                    ptp_status_updated = new_status
            except Exception as error:
                print(f"[followup_ptp_sync_error] {error}")
    return {"status": "success", "ptpStatusUpdated": ptp_status_updated}


@app.post("/api/followups/{followup_id}/reschedule")
def reschedule_followup(followup_id: str, payload: FollowUpRescheduleModel, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        rows = db.table("follow_ups").select(FOLLOWUP_SELECT).eq("id", followup_id).limit(1).execute().data
    except Exception as error:
        raise db_error(error)
    if not rows:
        raise HTTPException(404, "Follow-up not found")
    row = rows[0]
    if not can_view_followup(current_user, row):
        raise HTTPException(403, "You are not authorized to reschedule this follow-up")
    new_scheduled_at = optional_datetime(payload.newScheduledAt)
    if not new_scheduled_at:
        raise HTTPException(400, "A valid newScheduledAt date/time is required")
    try:
        new_id = with_retry(lambda: db.rpc("reschedule_follow_up", {
            "p_follow_up_id": followup_id, "p_agent_id": current_user["id"], "p_version": payload.version,
            "p_new_scheduled_at": new_scheduled_at, "p_reason": text(payload.reason), "p_notes": text(payload.notes),
        }).execute()).data
    except Exception as error:
        raise db_error(error)
    if not new_id:
        raise HTTPException(409, "This follow-up was already updated by someone else. Refresh and try again.")
    return {"status": "success", "newFollowUpId": new_id}


@app.post("/api/followups/{followup_id}/cancel")
def cancel_followup(followup_id: str, payload: FollowUpCancelModel, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        rows = db.table("follow_ups").select(FOLLOWUP_SELECT).eq("id", followup_id).limit(1).execute().data
    except Exception as error:
        raise db_error(error)
    if not rows:
        raise HTTPException(404, "Follow-up not found")
    row = rows[0]
    if not can_view_followup(current_user, row):
        raise HTTPException(403, "You are not authorized to cancel this follow-up")
    try:
        cancelled = with_retry(lambda: db.rpc("cancel_follow_up", {
            "p_follow_up_id": followup_id, "p_agent_id": current_user["id"], "p_version": payload.version, "p_reason": text(payload.reason),
        }).execute()).data
    except Exception as error:
        raise db_error(error)
    if not cancelled:
        raise HTTPException(409, "This follow-up was already updated by someone else. Refresh and try again.")
    return {"status": "success"}


@app.post("/api/followups/{followup_id}/reassign")
def reassign_followup(followup_id: str, payload: FollowUpReassignModel, current_user: dict = Depends(get_current_user)):
    if not can_allocate(current_user["role"]):
        raise HTTPException(403, "Only Team Leaders/Managers/Admins can reassign follow-ups")
    db = get_supabase()
    new_agent_id = resolve_agent_id_by_name(db, payload.newAgentName)
    if not new_agent_id:
        raise HTTPException(404, "Target agent not found")
    try:
        reassigned = with_retry(lambda: db.rpc("reassign_follow_up", {
            "p_follow_up_id": followup_id, "p_changed_by": current_user["id"], "p_version": payload.version,
            "p_new_agent_id": new_agent_id, "p_reason": text(payload.reason),
        }).execute()).data
    except Exception as error:
        raise db_error(error)
    if not reassigned:
        raise HTTPException(409, "This follow-up was already updated by someone else. Refresh and try again.")
    return {"status": "success"}


# --- PROMISE TO PAY ---
def can_view_ptp(current_user: Dict[str, Any], row: Dict[str, Any]) -> bool:
    if can_allocate(current_user.get("role", "")):
        return True
    return row.get("agent_id") == current_user.get("id")


@app.get("/api/ptp")
def list_ptp(status: Optional[str] = None, agentName: Optional[str] = None, campaignName: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        with_retry(lambda: db.rpc("expire_stale_ptps", {}).execute())
        query = db.table("promise_to_pay").select(PTP_SELECT)
        role = text(current_user.get("role", "")).lower()
        if role == "control agent":
            query = query.eq("agent_id", current_user["id"])
        elif agentName:
            agent_id = resolve_agent_id_by_name(db, agentName)
            query = query.eq("agent_id", agent_id or "00000000-0000-0000-0000-000000000000")
        if campaignName:
            campaigns = db.table("campaigns").select("id").eq("name", text(campaignName)).limit(1).execute().data
            query = query.eq("campaign_id", campaigns[0]["id"] if campaigns else "00000000-0000-0000-0000-000000000000")
        if status:
            query = query.eq("status", text(status).upper())
        rows = query.order("promised_date", desc=True).limit(1000).execute().data or []
    except Exception as error:
        raise db_error(error)
    return {"items": [followups_module.ptp_response(row) for row in rows]}


@app.get("/api/ptp/dashboard")
def ptp_dashboard(campaignName: Optional[str] = None, current_user: dict = Depends(get_current_user)):
    if not can_allocate(current_user["role"]):
        raise HTTPException(403, "Only Team Leaders/Managers/Admins can view PTP analytics")
    db = get_supabase()
    try:
        with_retry(lambda: db.rpc("expire_stale_ptps", {}).execute())
        query = db.table("promise_to_pay").select(PTP_SELECT)
        if campaignName:
            campaigns = db.table("campaigns").select("id").eq("name", text(campaignName)).limit(1).execute().data
            query = query.eq("campaign_id", campaigns[0]["id"] if campaigns else "00000000-0000-0000-0000-000000000000")
        rows = query.limit(5000).execute().data or []
    except Exception as error:
        raise db_error(error)
    return {
        "metrics": followups_module.ptp_dashboard_metrics(rows),
        "aging": followups_module.ptp_aging_buckets(rows),
        "byAgent": followups_module.agent_ptp_performance(rows),
        "byCampaign": followups_module.campaign_ptp_performance(rows),
    }


@app.post("/api/ptp")
def create_ptp_endpoint(payload: PtpCreateModel, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        customer_row = resolve_customer_360(db, payload.customerId, payload.campaignName)
    except Exception as error:
        raise db_error(error)
    if not customer_row:
        raise HTTPException(404, "Customer not found")
    if not can_view_customer(current_user, customer_row):
        raise HTTPException(403, "You are not authorized to record a PTP for this customer")
    weights = get_weights(db)
    validation = followups_module.validate_ptp_amount(payload.promisedAmount, customer_row.get("balance"), bool(weights.get("allow_ptp_exceeding_balance", True)))
    if not validation["ok"]:
        raise HTTPException(400, validation["error"])
    promised_date = optional_date(payload.promisedDate)
    if not promised_date:
        raise HTTPException(400, "A valid promisedDate is required")
    try:
        result = with_retry(lambda: db.rpc("create_ptp", {
            "p_agent_id": current_user["id"], "p_campaign_id": customer_row["campaign_id"], "p_customer_id": customer_row["customer_id"],
            "p_disposition_id": payload.dispositionId, "p_promised_amount": float(payload.promisedAmount), "p_promised_date": promised_date,
            "p_payment_method": text(payload.paymentMethod), "p_notes": text(payload.notes), "p_create_followup": payload.createFollowup,
            "p_followup_offset_minutes": int(weights.get("ptp_followup_offset_minutes", 0)),
        }).execute())
    except Exception as error:
        raise db_error(error)
    row = (result.data or [{}])[0] if isinstance(result.data, list) else {}
    return {"status": "success", "ptpId": row.get("ptp_id"), "followUpId": row.get("follow_up_id"), "warning": validation.get("warning")}


@app.get("/api/ptp/{ptp_id}")
def get_ptp(ptp_id: str, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        rows = db.table("promise_to_pay").select(PTP_SELECT).eq("id", ptp_id).limit(1).execute().data
    except Exception as error:
        raise db_error(error)
    if not rows:
        raise HTTPException(404, "PTP not found")
    if not can_view_ptp(current_user, rows[0]):
        raise HTTPException(403, "You are not authorized to view this PTP")
    return followups_module.ptp_response(rows[0])


@app.patch("/api/ptp/{ptp_id}")
def update_ptp(ptp_id: str, payload: PtpUpdateModel, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        rows = db.table("promise_to_pay").select(PTP_SELECT).eq("id", ptp_id).limit(1).execute().data
    except Exception as error:
        raise db_error(error)
    if not rows:
        raise HTTPException(404, "PTP not found")
    if not can_view_ptp(current_user, rows[0]):
        raise HTTPException(403, "You are not authorized to edit this PTP")
    updates: Dict[str, Any] = {}
    if payload.notes is not None:
        updates["notes"] = text(payload.notes)
    if payload.paymentMethod is not None:
        updates["payment_method"] = text(payload.paymentMethod)
    if not updates:
        return {"status": "success"}
    try:
        db.table("promise_to_pay").update(updates).eq("id", ptp_id).execute()
    except Exception as error:
        raise db_error(error)
    return {"status": "success"}


@app.post("/api/ptp/{ptp_id}/update-status")
def update_ptp_status_endpoint(ptp_id: str, payload: PtpStatusUpdateModel, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        rows = db.table("promise_to_pay").select(PTP_SELECT).eq("id", ptp_id).limit(1).execute().data
    except Exception as error:
        raise db_error(error)
    if not rows:
        raise HTTPException(404, "PTP not found")
    if not can_view_ptp(current_user, rows[0]):
        raise HTTPException(403, "You are not authorized to update this PTP")
    status_upper = text(payload.status).upper()
    if status_upper not in followups_module.PTP_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(followups_module.PTP_STATUSES))}")
    try:
        updated = with_retry(lambda: db.rpc("update_ptp_status", {
            "p_ptp_id": ptp_id, "p_agent_id": current_user["id"], "p_version": payload.version,
            "p_status": status_upper, "p_paid_amount": payload.paidAmount, "p_notes": text(payload.notes),
        }).execute()).data
    except Exception as error:
        raise db_error(error)
    if not updated:
        raise HTTPException(409, "This PTP was already updated by someone else. Refresh and try again.")
    return {"status": "success"}


# --- NOTIFICATIONS (computed on read; no persisted notifications table needed yet) ---
@app.get("/api/notifications")
def get_notifications(current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    try:
        with_retry(lambda: db.rpc("refresh_followup_states", {}).execute())
        with_retry(lambda: db.rpc("expire_stale_ptps", {}).execute())
        followup_query = db.table("follow_ups").select(FOLLOWUP_SELECT).in_("status", ["DUE", "OVERDUE"])
        ptp_query = db.table("promise_to_pay").select(PTP_SELECT).in_("status", list(followups_module.OPEN_PTP_STATUSES))
        role = text(current_user.get("role", "")).lower()
        if role == "control agent":
            followup_query = followup_query.eq("agent_id", current_user["id"])
            ptp_query = ptp_query.eq("agent_id", current_user["id"])
        followups_rows = followup_query.order("scheduled_at").limit(200).execute().data or []
        ptp_rows = ptp_query.execute().data or []
    except Exception as error:
        raise db_error(error)

    notifications = []
    for row in followups_rows:
        notifications.append({
            "type": "OVERDUE_FOLLOWUP" if row["status"] == "OVERDUE" else "FOLLOWUP_DUE_TODAY",
            "followUpId": row["id"], "customerId": row["customer_id"], "campaign": (row.get("campaign") or {}).get("name", ""),
            "scheduledAt": row["scheduled_at"],
        })
    for row in ptp_rows:
        promised_date = row.get("promised_date")
        is_overdue = promised_date and str(promised_date) < date.today().isoformat()
        notifications.append({
            "type": "PTP_OVERDUE" if is_overdue else "PTP_DUE_TODAY", "ptpId": row["id"], "customerId": row["customer_id"],
            "campaign": (row.get("campaign") or {}).get("name", ""), "promisedDate": promised_date, "amount": row.get("promised_amount"),
        })

    if can_allocate(current_user.get("role", "")):
        overdue_by_agent: Dict[str, int] = {}
        for row in followups_rows:
            if row["status"] == "OVERDUE":
                agent_name = (row.get("agent") or {}).get("name", "Unassigned")
                overdue_by_agent[agent_name] = overdue_by_agent.get(agent_name, 0) + 1
        for agent_name, count in overdue_by_agent.items():
            if count >= 5:
                notifications.append({"type": "AGENT_HAS_MANY_OVERDUE", "agent": agent_name, "count": count})

    return {"items": notifications, "total": len(notifications)}


# 1. Define your Models FIRST
class SheetsExportModel(BaseModel):
    rows: List[List[str]]
    campaignName: str = ""  

# 2. Define your Helper Functions NEXT
def get_gspread_client():
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    print(f"[DEBUG] GOOGLE_SERVICE_ACCOUNT_JSON Loaded: {bool(creds_json)}")
    if not creds_json:
        raise HTTPException(503, "Google Service Account credentials are not configured.")
    
    try:
        creds_dict = json.loads(creds_json)
        # This one simple line replaces all the manual Credentials logic!
        return gspread.service_account_from_dict(creds_dict)
    except Exception as e:
        print(f"[sheets_auth_error] {e}")
        raise HTTPException(500, f"Failed to authorize Google Sheets: {str(e)}")

@app.post("/api/disposition")
def submit_disposition(disp: DispositionModel, current_user: dict = Depends(get_current_user)):
    db = get_supabase()
    
    # --- UPDATED: Both Settled and Partial Payment count as real money collected ---
    safe_status = text(disp.status).lower()
    actual_recovered = disp.amountRec if safe_status in {"settled", "partial payment"} else 0.0

    try:
        result = db.rpc(
            "record_disposition", 
            {
                "p_customer_id": str(disp.customerId), 
                "p_outcome": str(disp.outcome or ""), 
                "p_status": str(disp.status or ""), 
                "p_amount_rec": float(actual_recovered), 
                "p_agent_name": str(disp.agentName or ""), 
                "p_comments": str(disp.comments or ""), 
                "p_business_status": str(disp.businessStatus or ""), 
                "p_ptp_time": str(disp.ptpTime or ""),
            }
        ).execute()
        
        # --- SUPER ROBUST UUID EXTRACTION ---
        raw_disp = result.data
        if isinstance(raw_disp, list) and len(raw_disp) > 0:
            raw_disp = raw_disp[0]
            
        if isinstance(raw_disp, dict):
            vals = list(raw_disp.values())
            disposition_id = str(vals[0]) if vals else None
        else:
            disposition_id = str(raw_disp) if raw_disp is not None else None
            
        if disposition_id in ["True", "False", "true", "false", "None"]:
            disposition_id = None
            
    except HTTPException:
        raise
    except Exception as error:
        print(f"\n[SUPABASE ERROR DETAILED]: {error}\n")
        raise HTTPException(status_code=500, detail=str(error))

    # The rest of the function remains exactly the same...
    ptp_info: Dict[str, Any] = {"created": False}
    is_ptp_status = text(disp.status).lower() in {"promise to pay (ptp)", "settled"}
    promised_date = optional_date((disp.ptpTime or "")[:10]) if disp.ptpTime else None
    
    if is_ptp_status and disp.amountRec and disp.amountRec > 0 and promised_date:
        try:
            customer_row = resolve_customer_360(db, disp.customerId, disp.campaignName)
            if customer_row:
                weights = get_weights(db)
                validation = followups_module.validate_ptp_amount(disp.amountRec, customer_row.get("balance"), bool(weights.get("allow_ptp_exceeding_balance", True)))
                
                if validation["ok"]:
                    ptp_result = with_retry(lambda: db.rpc("create_ptp", {
                        "p_agent_id": current_user["id"], 
                        "p_campaign_id": customer_row["campaign_id"], 
                        "p_customer_id": customer_row["customer_id"],
                        "p_disposition_id": disposition_id, 
                        "p_promised_amount": float(disp.amountRec), 
                        "p_promised_date": promised_date,
                        "p_payment_method": text(disp.ptpPaymentMethod), 
                        "p_notes": str(disp.comments or ""), 
                        "p_create_followup": True,
                        "p_followup_offset_minutes": int(weights.get("ptp_followup_offset_minutes", 0)),
                    }).execute())
                    
                    row = (ptp_result.data or [{}])[0] if isinstance(ptp_result.data, list) else (ptp_result.data or {})
                    ptp_info = {"created": True, "ptpId": row.get("ptp_id"), "followUpId": row.get("follow_up_id"), "warning": validation.get("warning")}
                else:
                    ptp_info = {"created": False, "error": validation.get("error")}
        except Exception as error:
            print(f"[auto_ptp_error] {error}")
            ptp_info = {"created": False, "error": f"Database Error: {str(error)}"}

    return {"status": "success", "data": disposition_id, "ptp": ptp_info}

@app.post("/api/export/sheets")
def export_to_sheets(payload: SheetsExportModel, current_user: dict = Depends(get_current_user)):
    user_email = current_user.get("email")
    if not user_email:
        raise HTTPException(400, "Your account does not have a valid email address.")
        
    try:
        client = get_gspread_client()
        template_id = os.getenv("GOOGLE_SHEET_TEMPLATE_ID", "").strip()
        
        if not template_id:
            raise HTTPException(500, "GOOGLE_SHEET_TEMPLATE_ID is missing from .env.local")
        
        # 1. Open the Master Sheet
        spreadsheet = client.open_by_key(template_id)
        worksheet = spreadsheet.sheet1
        
        # 2. FAST EXPORT: We only run a quick 'clear' and 'update' to minimize API calls
        if len(payload.rows) > 0:
            worksheet.clear()
            worksheet.update("A1", payload.rows)
            
        # 3. GENERATE THE CUSTOM TITLE
        timestamp = datetime.now().strftime("%b %d, %Y")
        safe_campaign = payload.campaignName.strip() if payload.campaignName else "Filtered Accounts"
        custom_title = f"{safe_campaign} Export - {timestamp}"
        encoded_title = urllib.parse.quote(custom_title)
            
        # 4. FORCE THE "MAKE A COPY" SCREEN
        copy_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}/copy?title={encoded_title}"
            
        return {
            "status": "success", 
            "sheetTitle": custom_title,
            "sheetUrl": copy_url
        }
        
    except Exception as error:
        print(f"[sheets_error] {error}")
        raise HTTPException(500, "Failed to export data to Google Sheets.")

# 4. Static Files and Fallbacks stay at the ABSOLUTE BOTTOM
CLEAN_PAGES = ["login", "overview", "workspace", "campaigns", "teamleader", "analytics", "admin", "index"]

if os.getenv("VERCEL") is None:
    from pathlib import Path
    from fastapi.responses import FileResponse, RedirectResponse

    # Resolve the root directory (one level up from /api)
    BASE_DIR = Path(__file__).resolve().parent.parent
    TEMPLATE_DIR = BASE_DIR / "template"

    def _register_clean_page_routes() -> None:
        for page in CLEAN_PAGES:
            file_name = f"{page}.html"

            def serve_page(file_name: str = file_name) -> FileResponse:
                # FIX 1: Point directly to the template folder
                return FileResponse(TEMPLATE_DIR / file_name)

            def redirect_to_clean_url(page: str = page) -> RedirectResponse:
                return RedirectResponse(url=f"/{page}", status_code=307)

            app.get(f"/{page}", include_in_schema=False)(serve_page)
            app.get(f"/{file_name}", include_in_schema=False)(redirect_to_clean_url)

    _register_clean_page_routes()
    
    # FIX 2: Explicitly map the CSS and JS files so the HTML can find them
    app.get("/callcenter.js", include_in_schema=False)(lambda: FileResponse(BASE_DIR / "static/js/callcenter.js"))
    app.get("/callcenter-tailwind.css", include_in_schema=False)(lambda: FileResponse(BASE_DIR / "static/css/callcenter-tailwind.css"))
    app.get("/callcenter.css", include_in_schema=False)(lambda: FileResponse(BASE_DIR / "static/css/callcenter.css"))
    app.get("/manifest.webmanifest", include_in_schema=False)(lambda: FileResponse(BASE_DIR / "manifest.webmanifest"))
    app.get("/service-worker.js", include_in_schema=False)(lambda: FileResponse(BASE_DIR / "service-worker.js"))
    app.mount("/", StaticFiles(directory=str(BASE_DIR), html=True), name="static")