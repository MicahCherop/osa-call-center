"""Customer 360 view-model helpers: contactability stats, PTP summary, follow-up state, and the
merged activity timeline. Kept separate from api/index.py (which owns auth/DB access) so the
shaping logic is unit-testable without a database.
"""
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

ANSWERED_OUTCOME = "answered"
OPEN_PTP_STATUSES = {"PENDING", "PARTIALLY_PAID"}
OPEN_FOLLOWUP_STATUSES = {"PENDING", "DUE", "OVERDUE"}


def _parse_datetime(value: Any):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(value: Any):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def build_summary(customer_row: Dict[str, Any], dispositions: List[Dict[str, Any]]) -> Dict[str, Any]:
    due_date = _parse_date(customer_row.get("due_date"))
    days_overdue = max((date.today() - due_date).days, 0) if due_date else None
    last_contact_at = customer_row.get("last_contact_at") or (dispositions[0]["created_at"] if dispositions else None)
    answered = sum(1 for row in dispositions if str(row.get("outcome") or "").strip().lower() == ANSWERED_OUTCOME)
    return {
        "outstandingBalance": customer_row.get("balance"),
        "daysOverdue": days_overdue,
        "lastContactAt": last_contact_at,
        "totalCalls": len(dispositions),
        "answeredCalls": answered,
        "campaign": (customer_row.get("campaign") or {}).get("name", ""),
        "assignedAgent": (customer_row.get("assigned_agent") or {}).get("name", ""),
        "status": customer_row.get("status") or "",
    }


def build_contactability(dispositions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts calls by their actual recorded outcome text (no invented categories)."""
    total = len(dispositions)
    breakdown: Dict[str, int] = {}
    answered = 0
    for row in dispositions:
        outcome = str(row.get("outcome") or "Unspecified").strip() or "Unspecified"
        breakdown[outcome] = breakdown.get(outcome, 0) + 1
        if outcome.strip().lower() == ANSWERED_OUTCOME:
            answered += 1
    contact_rate = round((answered / total) * 100, 1) if total else 0.0
    return {"attempts": total, "answered": answered, "breakdown": breakdown, "contactRate": contact_rate}


def build_ptp_history(dispositions: List[Dict[str, Any]], customer_row: Dict[str, Any]) -> Dict[str, Any]:
    ptp_rows = [row for row in dispositions if "promise to pay" in str(row.get("status") or "").lower()]
    total_promised = sum(float(row.get("amount_rec") or 0) for row in ptp_rows)
    # `total_paid` is the existing cumulative repayment field on the customer record; there is no
    # per-PTP fulfillment flag in the current schema, so outstanding is a best-effort derivation.
    paid = float(customer_row.get("total_paid") or 0)
    outstanding = max(round(total_promised - paid, 2), 0.0)
    history = [
        {
            "date": row.get("created_at"),
            "amount": row.get("amount_rec"),
            "ptpTime": row.get("ptp_time"),
            "agent": (row.get("agent") or {}).get("name", ""),
        }
        for row in ptp_rows
    ]
    return {"totalPromised": round(total_promised, 2), "paid": round(paid, 2), "outstanding": outstanding, "history": history}


def build_followups(dispositions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derives follow-up state from each disposition's recorded follow_up_at plus later activity."""
    now = datetime.now(timezone.utc)
    entries = [row for row in dispositions if row.get("follow_up_at")]
    followups = []
    for row in entries:
        follow_up_at = _parse_datetime(row.get("follow_up_at"))
        created_at = _parse_datetime(row.get("created_at"))
        later_contact = any(
            (_parse_datetime(other.get("created_at")) or now) > follow_up_at
            for other in dispositions
            if other is not row and (_parse_datetime(other.get("created_at")) or now) > (created_at or now)
        )
        if later_contact:
            state = "Completed"
        elif follow_up_at and follow_up_at < now:
            state = "Overdue"
        else:
            state = "Pending"
        followups.append({
            "followUpAt": row.get("follow_up_at"),
            "reason": row.get("status") or row.get("outcome") or "",
            "createdBy": (row.get("agent") or {}).get("name", ""),
            "state": state,
        })
    followups.sort(key=lambda item: item["followUpAt"] or "", reverse=True)
    return followups


def build_contact_history(dispositions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "date": row.get("created_at"),
            "outcome": row.get("outcome") or "",
            "status": row.get("status") or "",
            "amount": row.get("amount_rec"),
            "agent": (row.get("agent") or {}).get("name", ""),
            "notes": row.get("comments") or "",
            "ptpTime": row.get("ptp_time") or "",
            "followUpAt": row.get("follow_up_at"),
        }
        for row in dispositions
    ]


def build_timeline(
    dispositions: List[Dict[str, Any]],
    notes: List[Dict[str, Any]],
    audit_events: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for row in dispositions:
        events.append({
            "timestamp": row.get("created_at"),
            "type": "PTP" if "promise to pay" in str(row.get("status") or "").lower() else "DISPOSITION",
            "agent": (row.get("agent") or {}).get("name", ""),
            "outcome": row.get("outcome") or "",
            "notes": row.get("comments") or "",
            "amount": row.get("amount_rec"),
            "followUp": row.get("follow_up_at"),
        })
    for note in notes:
        events.append({
            "timestamp": note.get("created_at"),
            "type": "NOTE",
            "agent": (note.get("agent") or {}).get("name", ""),
            "outcome": None,
            "notes": note.get("note") or "",
            "amount": None,
            "followUp": None,
        })
    for audit in audit_events:
        action = audit.get("action") or ""
        if action not in {"CUSTOMER_RESERVED", "CUSTOMER_SKIPPED", "CUSTOMER_TAG_CHANGED"}:
            continue
        events.append({
            "timestamp": audit.get("created_at"),
            "type": "ASSIGNMENT" if action == "CUSTOMER_RESERVED" else "STATUS_CHANGE",
            "agent": (audit.get("agent") or {}).get("name", ""),
            "outcome": action.replace("CUSTOMER_", "").replace("_", " ").title(),
            "notes": audit.get("reason") or "",
            "amount": None,
            "followUp": None,
        })
    events.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return events


def paginate(items: List[Any], page: int, page_size: int) -> Tuple[List[Any], int, bool]:
    page = max(page, 1)
    page_size = max(min(page_size, 100), 1)
    start = (page - 1) * page_size
    end = start + page_size
    total = len(items)
    return items[start:end], total, end < total


# ---------------------------------------------------------------------------
# Normalized Follow-Up/PTP builders (follow_ups & promise_to_pay tables). Preferred over the
# disposition-derived heuristics above once a customer has real rows in those tables.
# ---------------------------------------------------------------------------

def current_followup(followups: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    open_items = [row for row in followups if row.get("status") in OPEN_FOLLOWUP_STATUSES]
    if not open_items:
        return None
    return min(open_items, key=lambda row: row.get("scheduled_at") or "")


def current_ptp(ptps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    open_items = [row for row in ptps if row.get("status") in OPEN_PTP_STATUSES]
    if not open_items:
        return None
    return max(open_items, key=lambda row: row.get("created_at") or "")


def build_followup_history_v2(followups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    history = [
        {
            "id": row.get("id"), "type": row.get("type"), "reason": row.get("reason") or "",
            "scheduledAt": row.get("scheduled_at"), "status": row.get("status"),
            "createdBy": (row.get("agent") or {}).get("name", ""), "completedAt": row.get("completed_at"),
            "completedOutcome": row.get("completed_outcome") or "", "rescheduledFrom": row.get("rescheduled_from"),
        }
        for row in followups
    ]
    history.sort(key=lambda item: item["scheduledAt"] or "", reverse=True)
    return history


def build_ptp_history_v2(ptps: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_promised = sum(float(row.get("promised_amount") or 0) for row in ptps)
    paid = sum(float(row.get("paid_amount") or 0) for row in ptps)
    outstanding = sum(float(row.get("remaining_amount") or 0) for row in ptps if row.get("status") in OPEN_PTP_STATUSES)
    history = [
        {
            "id": row.get("id"), "date": row.get("promised_date"), "amount": row.get("promised_amount"),
            "paidAmount": row.get("paid_amount"), "remainingAmount": row.get("remaining_amount"),
            "status": row.get("status"), "agent": (row.get("agent") or {}).get("name", ""),
        }
        for row in ptps
    ]
    history.sort(key=lambda item: item["date"] or "", reverse=True)
    return {"totalPromised": round(total_promised, 2), "paid": round(paid, 2), "outstanding": round(outstanding, 2), "history": history}

