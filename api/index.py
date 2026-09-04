import os
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TypeVar

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import Client, create_client

T = TypeVar("T")

app = FastAPI(title="OSA Call Center API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
_supabase: Optional[Client] = None


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
    print(f"[db_error] {error}")  # full detail stays server-side; clients only see a generic message
    return HTTPException(503, "A database request failed. Please try again or contact support.")


_TRANSIENT_MARKERS = ("timeout", "temporarily unavailable", "connection", "reset", "429", "502", "503", "504")


def with_retry(operation: Callable[[], T], attempts: int = 3, base_delay: float = 0.4) -> T:
    """Retries a Supabase call with backoff so brief DB blips don't surface as request failures."""
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


def agent_response(row: Dict[str, Any]) -> Dict[str, Any]:
    performance = row.get("performance") or {}
    return {"id": row["id"], "name": row["name"], "email": row["email"], "role": row["role"], "status": row["status"], "callsMade": performance.get("calls_made", 0), "connected": performance.get("connected", 0), "conversion": float(performance.get("conversion") or 0)}


def customer_response(row: Dict[str, Any]) -> Dict[str, Any]:
    campaign, agent = row.get("campaign") or {}, row.get("assigned_agent") or {}
    return {"id": row["customer_id"], "name": row["name"], "phone": row["phone"], "branch": row["branch"], "sector": row["sector"], "balance": row["balance"] or "", "campaign": campaign.get("name", ""), "agentId": agent.get("name", ""), "worked": "TRUE" if row["worked"] else "FALSE", "outcome": row["outcome"], "status": row["status"], "dueDate": row["due_date"] or "", "pair": row["pair"], "disbAmount": row["disb_amount"] or "", "totalPaid": row["total_paid"] or "", "businessStatus": row["business_status"], "ptpAmount": row["ptp_amount"] or "", "ptpTime": row["ptp_time"], "feedback": row["feedback"], "daysInactive": row["days_inactive"] or "", "daysDormant": row["days_dormant"] or "", "loyalty": row["loyalty"], "lastLoanAmount": row["last_loan_amount"] or "", "sourceData": row.get("source_data") or {}}


class UserCreateModel(BaseModel):
    name: str
    email: str
    role: str
    status: str = "Active"
    requesterRole: str = ""


class UserEditModel(BaseModel):
    email: str
    name: str
    role: str
    status: str = "Active"
    requesterRole: str = ""


class UserDeleteModel(BaseModel):
    email: str
    requesterRole: str = ""


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


class ClaimNextCustomerModel(BaseModel):
    agentName: str
    requesterRole: str


class AdminCampaignUpdateModel(BaseModel):
    name: str
    type: str
    priority: str
    startDate: str = ""
    endDate: str = ""
    archived: bool = False
    requesterRole: str = ""


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
    requesterRole: str = ""


class AdminDispositionUpdateModel(BaseModel):
    id: str
    outcome: str
    status: str = ""
    amountRec: float = 0.0
    comments: str = ""
    businessStatus: str = ""
    ptpTime: str = ""
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


@app.get("/")
@app.get("/api")
def read_root():
    return {"message": "FastAPI Server is running successfully on Supabase!"}


@app.get("/health")
@app.get("/api/health")
def health_check():
    configured = bool(os.getenv("SUPABASE_URL", "").strip() and os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip())
    return {"status": "ok" if configured else "configuration_required", "message": "Call Center API is running", "supabaseConfigured": configured}


@app.post("/login")
@app.post("/api/login")
def login(creds: LoginModel):
    email = text(creds.email).lower()
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
def get_agents():
    try:
        return [agent_response(row) for row in get_supabase().table("agents").select("*,performance:control_agent_performance(calls_made,connected,conversion)").order("name").execute().data]
    except Exception as error:
        raise db_error(error)


@app.post("/api/users/add")
def add_user(user: UserCreateModel):
    if not can_allocate(user.requesterRole):
        raise HTTPException(403, "Only managers can create users")
    if text(user.requesterRole).lower() == "team leader" and text(user.role).lower() != "control agent":
        raise HTTPException(403, "Team Leaders may only create Control Agents")
    try:
        get_supabase().table("agents").insert({"name": text(user.name), "email": text(user.email).lower(), "role": text(user.role), "status": text(user.status)}).execute()
    except Exception as error:
        if "duplicate" in str(error).lower():
            raise HTTPException(400, "A user with this email already exists.")
        raise db_error(error)
    return {"status": "success", "message": f"User {user.name} created successfully"}


@app.post("/api/users/edit")
def edit_user(user: UserEditModel):
    if not can_administer(user.requesterRole):
        raise HTTPException(403, "Only Admin or Ops Manager can edit users")
    try:
        result = get_supabase().table("agents").update({"name": text(user.name), "role": text(user.role), "status": text(user.status)}).eq("email", text(user.email).lower()).execute()
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "User not found in database.")
    return {"success": True}


@app.post("/api/users/delete")
def delete_user(user: UserDeleteModel):
    if not can_administer(user.requesterRole):
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
def update_agent_status(name: str = Body(...), status: str = Body(...)):
    try:
        result = get_supabase().table("agents").update({"status": text(status)}).eq("name", text(name)).execute()
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Agent not found in database")
    return {"status": "success"}


@app.get("/api/campaigns")
def get_campaigns(fresh: bool = False):
    try:
        rows = get_supabase().table("campaign_summary").select("*").order("date_added", desc=True).execute().data
        return [{"name": row["name"], "type": row["type"], "priority": row["priority"], "startDate": row["start_date"] or "", "endDate": row["end_date"] or "", "dateAdded": row["date_added"] or "", "archivedAt": row["archived_at"] or "", "accountCount": row["account_count"]} for row in rows]
    except Exception as error:
        raise db_error(error)


@app.post("/campaigns")
@app.post("/api/campaigns")
def create_campaign(name: str = Body(...), type: str = Body(...), priority: str = Body(...), startDate: str = Body(""), endDate: str = Body(""), customers: List[CustomerUploadModel] = Body(...), chunkIndex: int = Body(0)):
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
def get_admin_dispositions(campaignName: Optional[str] = None, requesterRole: str = ""):
    if not can_administer(requesterRole):
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
def update_admin_campaign(campaign_name: str, campaign: AdminCampaignUpdateModel):
    if not can_administer(campaign.requesterRole):
        raise HTTPException(403, "Only Admin or Ops Manager can modify campaigns")
    try:
        result = get_supabase().table("campaigns").update({"name": text(campaign.name), "type": text(campaign.type), "priority": text(campaign.priority), "start_date": optional_date(campaign.startDate), "end_date": optional_date(campaign.endDate), "archived_at": datetime.now(timezone.utc).isoformat() if campaign.archived else None}).eq("name", text(campaign_name)).execute()
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Campaign not found")
    return {"status": "success"}


@app.patch("/api/admin/customers")
def update_admin_customer(customer: AdminCustomerUpdateModel):
    if not can_administer(customer.requesterRole):
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
def update_admin_disposition(disposition_id: str, disposition: AdminDispositionUpdateModel):
    if not can_administer(disposition.requesterRole):
        raise HTTPException(403, "Only Admin or Ops Manager can modify dispositions")
    try:
        result = get_supabase().table("dispositions").update({"outcome": text(disposition.outcome), "status": text(disposition.status), "amount_rec": disposition.amountRec, "comments": text(disposition.comments), "business_status": text(disposition.businessStatus), "ptp_time": text(disposition.ptpTime)}).eq("id", disposition_id).execute()
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Disposition not found")
    return {"status": "success"}


CUSTOMER_SELECT = "campaign_id,customer_id,name,phone,branch,sector,balance,due_date,pair,disb_amount,total_paid,worked,outcome,status,business_status,ptp_amount,ptp_time,feedback,days_inactive,days_dormant,loyalty,last_loan_amount,source_data,campaign:campaigns(name),assigned_agent:agents!customers_assigned_agent_id_fkey(name)"


@app.get("/customers")
@app.get("/api/customers")
def get_customers(agentName: Optional[str] = None, campaignName: Optional[str] = None, pending: bool = False, offset: int = 0, limit: int = 200):
    if offset < 0 or limit < 1 or limit > 500:
        raise HTTPException(400, "offset must be >= 0 and limit must be between 1 and 500")
    try:
        db, agent_id = get_supabase(), None
        if agentName:
            agents = db.table("agents").select("id").eq("name", text(agentName)).limit(1).execute().data
            if not agents:
                return {"items": [], "offset": offset, "limit": limit, "total": 0, "hasMore": False}
            agent_id = agents[0]["id"]
        campaign_id = None
        if campaignName:
            campaigns = db.table("campaigns").select("id").eq("name", text(campaignName)).limit(1).execute().data
            if not campaigns:
                return {"items": [], "offset": offset, "limit": limit, "total": 0, "hasMore": False}
            campaign_id = campaigns[0]["id"]
        query = db.table("customers").select(CUSTOMER_SELECT, count="exact")
        if agent_id:
            query = query.eq("assigned_agent_id", agent_id)
            query = query.eq("worked", True) if pending else query.eq("worked", False)
        if campaign_id:
            query = query.eq("campaign_id", campaign_id)
        result = query.order("created_at").range(offset, offset + limit - 1).execute()
    except Exception as error:
        raise db_error(error)
    rows = [row for row in result.data if not pending or text(row.get("outcome")).lower() not in {"", "answered"}]
    total = len(rows) if pending else result.count or 0
    return {"items": [customer_response(row) for row in rows], "offset": offset, "limit": limit, "total": total, "hasMore": offset + len(rows) < total}


@app.get("/api/ptps")
def get_agent_ptps(agentName: str, campaignName: Optional[str] = None):
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


@app.post("/assign")
@app.post("/api/assign")
def assign_customer(assignment: CustomerAssignModel):
    if not can_allocate(assignment.requesterRole):
        raise HTTPException(403, "Only managers can assign accounts")
    try:
        result = with_retry(lambda: get_supabase().rpc("assign_customer", {"p_customer_id": assignment.customerId, "p_agent_name": assignment.agentName}).execute())
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Customer or agent not found")
    return {"status": "success", "customerId": assignment.customerId, "agentName": assignment.agentName}


@app.post("/api/claim-next-customer")
def claim_next_customer(claim: ClaimNextCustomerModel):
    if text(claim.requesterRole) != "Admin":
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


@app.post("/distribute")
@app.post("/api/distribute")
def distribute_customers(distribution: DistributionModel):
    if not can_allocate(distribution.requesterRole):
        raise HTTPException(403, "Only managers can assign accounts")
    if not distribution.selectedAgents:
        raise HTTPException(400, "Select at least one active agent")
    try:
        assigned = int(with_retry(lambda: get_supabase().rpc("distribute_campaign", {"p_campaign_name": distribution.campaign, "p_agent_names": distribution.selectedAgents}).execute()).data or 0)
    except Exception as error:
        raise db_error(error)
    return {"status": "success", "assignedCount": assigned} if assigned else {"status": "info", "message": "No unassigned customers remaining"}


@app.post("/disposition")
@app.post("/api/disposition")
def submit_disposition(disp: DispositionModel):
    try:
        result = with_retry(lambda: get_supabase().rpc("record_disposition", {"p_customer_id": disp.customerId, "p_outcome": disp.outcome, "p_status": disp.status, "p_amount_rec": disp.amountRec, "p_agent_name": disp.agentName, "p_comments": disp.comments, "p_business_status": disp.businessStatus, "p_ptp_time": disp.ptpTime}).execute())
    except Exception as error:
        raise db_error(error)
    if not result.data:
        raise HTTPException(404, "Customer or agent not found")
    return {"status": "success"}


CLEAN_PAGES = ["login", "overview", "workspace", "campaigns", "teamleader", "analytics", "admin", "index"]

if os.getenv("VERCEL") is None:
    from fastapi.responses import FileResponse, RedirectResponse

    def _register_clean_page_routes() -> None:
        for page in CLEAN_PAGES:
            file_name = f"{page}.html"

            def serve_page(file_name: str = file_name) -> FileResponse:
                return FileResponse(file_name)

            def redirect_to_clean_url(page: str = page) -> RedirectResponse:
                return RedirectResponse(url=f"/{page}", status_code=307)

            app.get(f"/{page}", include_in_schema=False)(serve_page)
            app.get(f"/{file_name}", include_in_schema=False)(redirect_to_clean_url)

    _register_clean_page_routes()
    app.mount("/", StaticFiles(directory=".", html=True), name="static")