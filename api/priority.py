"""Priority scoring service for the Next Customer queue.

The backend is authoritative for scoring: candidates are filtered server-side (by
assigned_agent_id/worked via an indexed query) and every candidate's score is computed here in
Python before a customer is reserved. The frontend never calculates or receives raw weights.

Score = sum(weight * normalized_component[0..100]) - penalties, clamped to [0, 100].
Weights are fractional (roughly summing to 1.0 across positive factors) and are stored in the
`queue_priority_weights` table so they can be tuned without a deploy. See the migration
20260904000000_next_customer_queue.sql for the schema and defaults.
"""
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Tuple

DEFAULT_WEIGHTS: Dict[str, float] = {
    "campaign_priority_weight": 0.20,
    "overdue_days_weight": 0.20,
    "ptp_weight": 0.25,
    "followup_weight": 0.15,
    "balance_weight": 0.10,
    "contactability_weight": 0.05,
    "recent_contact_penalty": 0.05,
    "attempt_penalty": 0.10,
    "overdue_followup_weight": 0.10,
    "ptp_overdue_weight": 0.10,
    "recent_contact_hours": 4,
    "max_attempts": 6,
    "lock_minutes": 12,
    "ptp_followup_offset_minutes": 0,
    "allow_ptp_exceeding_balance": True,
    "ptp_expiry_grace_days": 7,
    "auto_confirm_ptp_outcomes": True,
}

OPEN_PTP_STATUSES = {"PENDING", "PARTIALLY_PAID"}
OPEN_FOLLOWUP_STATUSES = {"PENDING", "DUE", "OVERDUE"}

_WEIGHTS_CACHE: Dict[str, Any] = {"data": None, "expires_at": 0.0}
_WEIGHTS_CACHE_TTL_SECONDS = 60.0


def get_weights(db) -> Dict[str, Any]:
    """Returns the active scoring weights, cached briefly to avoid a DB round-trip per request."""
    now = time.monotonic()
    if _WEIGHTS_CACHE["data"] is not None and _WEIGHTS_CACHE["expires_at"] > now:
        return _WEIGHTS_CACHE["data"]
    weights = dict(DEFAULT_WEIGHTS)
    try:
        rows = db.table("queue_priority_weights").select("*").eq("id", 1).limit(1).execute().data
        if rows:
            weights.update({key: value for key, value in rows[0].items() if key in DEFAULT_WEIGHTS})
    except Exception:
        pass
    _WEIGHTS_CACHE["data"] = weights
    _WEIGHTS_CACHE["expires_at"] = now + _WEIGHTS_CACHE_TTL_SECONDS
    return weights


def invalidate_weights_cache() -> None:
    _WEIGHTS_CACHE["data"] = None
    _WEIGHTS_CACHE["expires_at"] = 0.0


def _parse_date(value: Any) -> Any:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _campaign_priority_score(priority: str) -> float:
    return {"high": 100.0, "medium": 60.0, "low": 30.0}.get(str(priority or "").strip().lower(), 50.0)


def is_eligible(row: Dict[str, Any], weights: Dict[str, Any], followup: Dict[str, Any] = None) -> bool:
    """Applies hard eligibility rules that are not simple score penalties.

    `followup` is the normalized follow_ups row (if any) linked to this candidate; when provided
    it takes precedence over the legacy customers.follow_up_at column for the future-date check.
    """
    if row.get("worked"):
        return False
    lock_expires_at = _parse_datetime(row.get("lock_expires_at"))
    if lock_expires_at and lock_expires_at > datetime.now(timezone.utc):
        return False
    if followup is not None:
        if followup.get("status") == "PENDING":
            scheduled_at = _parse_datetime(followup.get("scheduled_at"))
            if scheduled_at and scheduled_at > datetime.now(timezone.utc):
                return False
    else:
        follow_up_at = _parse_datetime(row.get("follow_up_at"))
        if follow_up_at and follow_up_at > datetime.now(timezone.utc):
            return False
    max_attempts = max(int(weights.get("max_attempts", 6)), 1)
    if int(row.get("attempts") or 0) >= max_attempts:
        return False
    return True


def score_customer(row: Dict[str, Any], weights: Dict[str, Any], followup: Dict[str, Any] = None, ptp: Dict[str, Any] = None) -> Tuple[float, List[str]]:
    """Returns (priority_score 0-100, human-readable reasons) for a single candidate row.

    `followup`/`ptp` are the normalized follow_ups/promise_to_pay rows linked to this candidate
    (if any); when omitted, scoring falls back to the legacy customers.status/follow_up_at fields
    so existing callers/tests keep working unchanged.
    """
    reasons: List[str] = []

    campaign_priority = str((row.get("campaign") or {}).get("priority") or "").strip().lower()
    campaign_score = _campaign_priority_score(campaign_priority)
    if campaign_priority == "high":
        reasons.append("High campaign priority")

    due_date = _parse_date(row.get("due_date"))
    overdue_days = (date.today() - due_date).days if due_date else 0
    overdue_score = max(0.0, min(overdue_days, 30) / 30.0 * 100.0)
    if overdue_days > 0:
        reasons.append(f"{overdue_days} days overdue")

    if ptp is not None:
        is_ptp = ptp.get("status") in OPEN_PTP_STATUSES
        promised_date = _parse_date(ptp.get("promised_date"))
        ptp_overdue = bool(is_ptp and promised_date and promised_date < date.today())
    else:
        is_ptp = "promise to pay" in str(row.get("status") or "").lower()
        ptp_overdue = False
    ptp_score = 100.0 if is_ptp else 0.0
    ptp_overdue_score = 100.0 if ptp_overdue else 0.0
    if is_ptp:
        reasons.append("Outstanding promise to pay")
    if ptp_overdue:
        reasons.append("Promise to pay is overdue")

    if followup is not None:
        followup_status = followup.get("status")
        followup_due = followup_status in {"DUE", "OVERDUE"}
        followup_overdue = followup_status == "OVERDUE"
    else:
        follow_up_at = _parse_datetime(row.get("follow_up_at"))
        followup_due = bool(follow_up_at and follow_up_at <= datetime.now(timezone.utc))
        followup_overdue = False
    followup_score = 100.0 if followup_due else 0.0
    overdue_followup_score = 100.0 if followup_overdue else 0.0
    if followup_overdue:
        reasons.append("Follow-up is overdue")
    elif followup_due:
        reasons.append("Follow-up due today")

    balance = float(row.get("balance") or 0)
    balance_score = max(0.0, min(balance / 100000.0 * 100.0, 100.0))
    if balance >= 50000:
        reasons.append("High outstanding balance")

    last_outcome = str(row.get("outcome") or "").strip().lower()
    contactability_score = 100.0 if last_outcome == "answered" else (50.0 if last_outcome else 20.0)

    last_contact_at = _parse_datetime(row.get("last_contact_at"))
    recent_contact_penalty_score = 0.0
    if last_contact_at:
        hours_since = (datetime.now(timezone.utc) - last_contact_at).total_seconds() / 3600.0
        if hours_since < float(weights.get("recent_contact_hours", 4)):
            recent_contact_penalty_score = 100.0

    attempts = int(row.get("attempts") or 0)
    max_attempts = max(int(weights.get("max_attempts", 6)), 1)
    attempt_penalty_score = max(0.0, min(attempts / max_attempts * 100.0, 100.0))

    total = (
        weights["campaign_priority_weight"] * campaign_score
        + weights["overdue_days_weight"] * overdue_score
        + weights["ptp_weight"] * ptp_score
        + weights["followup_weight"] * followup_score
        + weights.get("overdue_followup_weight", 0) * overdue_followup_score
        + weights.get("ptp_overdue_weight", 0) * ptp_overdue_score
        + weights["balance_weight"] * balance_score
        + weights["contactability_weight"] * contactability_score
        - weights["recent_contact_penalty"] * recent_contact_penalty_score
        - weights["attempt_penalty"] * attempt_penalty_score
    )
    score = max(0.0, min(round(total, 1), 100.0))
    return score, reasons


def candidate_key(row: Dict[str, Any]) -> Tuple[Any, Any]:
    return (row.get("campaign_id"), row.get("customer_id"))


def rank_candidates(
    rows: List[Dict[str, Any]], weights: Dict[str, Any],
    followups_by_key: Dict[Tuple[Any, Any], Dict[str, Any]] = None,
    ptps_by_key: Dict[Tuple[Any, Any], Dict[str, Any]] = None,
) -> List[Tuple[float, List[str], Dict[str, Any]]]:
    """Filters to eligible rows, scores them, and returns them sorted best-first.

    `followups_by_key`/`ptps_by_key` map (campaign_id, customer_id) -> open follow_ups/
    promise_to_pay row, letting the queue use the normalized tables when available.
    """
    scored = []
    for row in rows:
        key = candidate_key(row)
        followup = followups_by_key.get(key) if followups_by_key else None
        ptp = ptps_by_key.get(key) if ptps_by_key else None
        if not is_eligible(row, weights, followup):
            continue
        score, reasons = score_customer(row, weights, followup, ptp)
        scored.append((score, reasons, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored
