"""Follow-Up and Promise-to-Pay view-model helpers: response shaping and aggregate dashboards.
Kept free of DB access so the math is unit-testable without a database (mirrors priority.py /
customer360.py).
"""
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

FOLLOWUP_TYPES = {"CALLBACK", "PTP", "PAYMENT_CHECK", "DOCUMENT_REQUEST", "SUPERVISOR_FOLLOWUP", "OTHER"}
FOLLOWUP_STATUSES = {"PENDING", "DUE", "OVERDUE", "IN_PROGRESS", "COMPLETED", "CANCELLED", "RESCHEDULED"}
PTP_STATUSES = {"PENDING", "PARTIALLY_PAID", "FULFILLED", "BROKEN", "CANCELLED", "EXPIRED"}
OPEN_PTP_STATUSES = {"PENDING", "PARTIALLY_PAID"}
OPEN_FOLLOWUP_STATUSES = {"PENDING", "DUE", "OVERDUE"}


def _parse_date(value: Any):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def followup_response(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"], "customerId": row.get("customer_id"), "campaign": (row.get("campaign") or {}).get("name", ""),
        "agent": (row.get("agent") or {}).get("name", ""), "type": row.get("type"), "reason": row.get("reason") or "",
        "scheduledAt": row.get("scheduled_at"), "status": row.get("status"), "priority": row.get("priority") or "medium",
        "notes": row.get("notes") or "", "completedAt": row.get("completed_at"), "completedOutcome": row.get("completed_outcome") or "",
        "ptpId": row.get("ptp_id"), "rescheduledFrom": row.get("rescheduled_from"), "version": row.get("version", 1),
    }


def ptp_response(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"], "customerId": row.get("customer_id"), "campaign": (row.get("campaign") or {}).get("name", ""),
        "agent": (row.get("agent") or {}).get("name", ""), "promisedAmount": row.get("promised_amount"),
        "promisedDate": row.get("promised_date"), "paymentMethod": row.get("payment_method") or "",
        "status": row.get("status"), "paidAmount": row.get("paid_amount"), "remainingAmount": row.get("remaining_amount"),
        "notes": row.get("notes") or "", "followUpId": row.get("follow_up_id"), "version": row.get("version", 1),
    }


def bucket_followups(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Groups by workflow status; DUE/OVERDUE are kept current via refresh_followup_states()."""
    buckets: Dict[str, List[Dict[str, Any]]] = {"today": [], "overdue": [], "upcoming": [], "completed": []}
    for row in rows:
        status = row.get("status")
        if status == "DUE":
            buckets["today"].append(row)
        elif status == "OVERDUE":
            buckets["overdue"].append(row)
        elif status == "PENDING":
            buckets["upcoming"].append(row)
        elif status == "COMPLETED":
            buckets["completed"].append(row)
    for key in ("today", "overdue", "upcoming"):
        buckets[key].sort(key=lambda row: row.get("scheduled_at") or "")
    buckets["completed"].sort(key=lambda row: row.get("completed_at") or "", reverse=True)
    return buckets


def followup_summary_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    tomorrow = None
    today = date.today()
    counts = {"dueToday": 0, "overdue": 0, "tomorrow": 0, "completed": 0}
    for row in rows:
        status = row.get("status")
        if status == "DUE":
            counts["dueToday"] += 1
        elif status == "OVERDUE":
            counts["overdue"] += 1
        elif status == "COMPLETED":
            counts["completed"] += 1
        elif status == "PENDING":
            scheduled = _parse_date(row.get("scheduled_at"))
            if scheduled and (scheduled - today).days == 1:
                counts["tomorrow"] += 1
    return counts


def validate_ptp_amount(promised_amount: Optional[float], outstanding_balance: Optional[float], allow_exceeding: bool) -> Dict[str, Any]:
    """Returns {"ok": bool, "warning": str|None, "error": str|None}. Never silently changes the amount."""
    if promised_amount is None or promised_amount < 0:
        return {"ok": False, "warning": None, "error": "Promised amount must be zero or greater."}
    if outstanding_balance is not None and promised_amount > float(outstanding_balance):
        if allow_exceeding:
            return {"ok": True, "warning": f"Promised amount exceeds the outstanding balance of {outstanding_balance}.", "error": None}
        return {"ok": False, "warning": None, "error": "Promised amount cannot exceed the outstanding balance."}
    return {"ok": True, "warning": None, "error": None}


def ptp_dashboard_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_value = sum(float(row.get("promised_amount") or 0) for row in rows)
    collected = sum(float(row.get("paid_amount") or 0) for row in rows)
    outstanding = sum(float(row.get("remaining_amount") or 0) for row in rows if row.get("status") in OPEN_PTP_STATUSES)
    resolved = [row for row in rows if row.get("status") in {"FULFILLED", "BROKEN", "EXPIRED"}]
    fulfilled = sum(1 for row in resolved if row.get("status") == "FULFILLED")
    broken = sum(1 for row in resolved if row.get("status") in {"BROKEN", "EXPIRED"})
    success_rate = round((fulfilled / len(resolved)) * 100, 1) if resolved else 0.0
    broken_rate = round((broken / len(resolved)) * 100, 1) if resolved else 0.0
    return {
        "totalValue": round(total_value, 2), "collected": round(collected, 2), "outstanding": round(outstanding, 2),
        "successRate": success_rate, "brokenRate": broken_rate, "totalCount": len(rows),
    }


def ptp_aging_buckets(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    today = date.today()
    buckets = {"dueToday": 0.0, "days1to3": 0.0, "days4to7": 0.0, "days8to30": 0.0, "days30plus": 0.0}
    for row in rows:
        if row.get("status") not in OPEN_PTP_STATUSES:
            continue
        promised_date = _parse_date(row.get("promised_date"))
        if not promised_date:
            continue
        overdue_days = (today - promised_date).days
        amount = float(row.get("remaining_amount") if row.get("remaining_amount") is not None else row.get("promised_amount") or 0)
        if overdue_days <= 0:
            buckets["dueToday"] += amount
        elif overdue_days <= 3:
            buckets["days1to3"] += amount
        elif overdue_days <= 7:
            buckets["days4to7"] += amount
        elif overdue_days <= 30:
            buckets["days8to30"] += amount
        else:
            buckets["days30plus"] += amount
    return {key: round(value, 2) for key, value in buckets.items()}


def _group_performance(rows: List[Dict[str, Any]], key_fn) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = key_fn(row)
        if not key:
            continue
        group = groups.setdefault(key, {"count": 0, "value": 0.0, "fulfilled": 0, "broken": 0, "collected": 0.0})
        group["count"] += 1
        group["value"] += float(row.get("promised_amount") or 0)
        group["collected"] += float(row.get("paid_amount") or 0)
        if row.get("status") == "FULFILLED":
            group["fulfilled"] += 1
        elif row.get("status") in {"BROKEN", "EXPIRED"}:
            group["broken"] += 1
    for group in groups.values():
        resolved = group["fulfilled"] + group["broken"]
        group["successRate"] = round((group["fulfilled"] / resolved) * 100, 1) if resolved else 0.0
        group["value"] = round(group["value"], 2)
        group["collected"] = round(group["collected"], 2)
    return groups


def agent_ptp_performance(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return _group_performance(rows, lambda row: (row.get("agent") or {}).get("name", ""))


def campaign_ptp_performance(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return _group_performance(rows, lambda row: (row.get("campaign") or {}).get("name", ""))
