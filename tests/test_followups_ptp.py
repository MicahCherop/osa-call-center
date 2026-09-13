"""Tests for the Follow-Up and Promise-to-Pay management system.

Run with: pytest tests/test_followups_ptp.py -v
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import followups as followups_module  # noqa: E402
from api import priority  # noqa: E402
from api.index import app, get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Pure helper unit tests (api/followups.py) - no mocking required.
# ---------------------------------------------------------------------------

def make_followup(**overrides):
    row = {
        "id": "f1", "campaign_id": "camp-1", "customer_id": "1001", "agent_id": "agent-1",
        "type": "CALLBACK", "reason": "", "scheduled_at": datetime.now(timezone.utc).isoformat(),
        "status": "PENDING", "priority": "medium", "notes": "", "completed_at": None, "completed_outcome": "",
        "ptp_id": None, "rescheduled_from": None, "version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "agent": {"name": "Mary"}, "campaign": {"name": "DD1-DD7"},
    }
    row.update(overrides)
    return row


def make_ptp(**overrides):
    row = {
        "id": "p1", "campaign_id": "camp-1", "customer_id": "1001", "agent_id": "agent-1",
        "promised_amount": 5000, "promised_date": date.today().isoformat(), "payment_method": "",
        "status": "PENDING", "paid_amount": 0, "remaining_amount": 5000, "notes": "", "version": 1,
        "agent": {"name": "Mary"}, "campaign": {"name": "DD1-DD7"},
    }
    row.update(overrides)
    return row


def test_validate_ptp_amount_rejects_negative():
    result = followups_module.validate_ptp_amount(-100, 10000, True)
    assert result["ok"] is False


def test_validate_ptp_amount_warns_when_exceeding_balance_and_allowed():
    result = followups_module.validate_ptp_amount(15000, 10000, True)
    assert result["ok"] is True
    assert result["warning"] is not None


def test_validate_ptp_amount_rejects_when_exceeding_balance_and_not_allowed():
    result = followups_module.validate_ptp_amount(15000, 10000, False)
    assert result["ok"] is False
    assert result["error"] is not None


def test_ptp_dashboard_metrics_calculates_outstanding_and_rates():
    rows = [
        make_ptp(id="a", status="FULFILLED", promised_amount=5000, paid_amount=5000, remaining_amount=0),
        make_ptp(id="b", status="BROKEN", promised_amount=3000, paid_amount=0, remaining_amount=3000),
        make_ptp(id="c", status="PENDING", promised_amount=2000, paid_amount=0, remaining_amount=2000),
    ]
    metrics = followups_module.ptp_dashboard_metrics(rows)
    assert metrics["totalValue"] == 10000
    assert metrics["collected"] == 5000
    assert metrics["outstanding"] == 2000
    assert metrics["successRate"] == 50.0
    assert metrics["brokenRate"] == 50.0


def test_ptp_aging_buckets_group_by_overdue_days():
    rows = [
        make_ptp(id="a", promised_date=date.today().isoformat(), remaining_amount=100),
        make_ptp(id="b", promised_date=(date.today() - timedelta(days=2)).isoformat(), remaining_amount=200),
        make_ptp(id="c", promised_date=(date.today() - timedelta(days=40)).isoformat(), remaining_amount=300),
    ]
    aging = followups_module.ptp_aging_buckets(rows)
    assert aging["dueToday"] == 100
    assert aging["days1to3"] == 200
    assert aging["days30plus"] == 300


def test_bucket_followups_groups_by_status():
    rows = [
        make_followup(id="a", status="DUE"),
        make_followup(id="b", status="OVERDUE"),
        make_followup(id="c", status="PENDING"),
        make_followup(id="d", status="COMPLETED"),
    ]
    buckets = followups_module.bucket_followups(rows)
    assert [row["id"] for row in buckets["today"]] == ["a"]
    assert [row["id"] for row in buckets["overdue"]] == ["b"]
    assert [row["id"] for row in buckets["upcoming"]] == ["c"]
    assert [row["id"] for row in buckets["completed"]] == ["d"]


# ---------------------------------------------------------------------------
# Priority integration: due/overdue follow-ups and PTPs should raise Next Customer scores.
# ---------------------------------------------------------------------------

def make_customer_row(**overrides):
    row = {
        "campaign_id": "camp-1", "customer_id": "1001", "worked": False, "balance": 1000, "due_date": None,
        "status": "", "outcome": "", "attempts": 0, "last_contact_at": None, "follow_up_at": None,
        "locked_by": None, "lock_expires_at": None, "campaign": {"name": "c", "priority": "medium"},
    }
    row.update(overrides)
    return row


def test_due_followup_outranks_no_followup():
    weights = dict(priority.DEFAULT_WEIGHTS)
    plain = make_customer_row()
    followup_due = {"status": "DUE", "scheduled_at": datetime.now(timezone.utc).isoformat()}
    score_plain, _ = priority.score_customer(plain, weights)
    score_due, reasons_due = priority.score_customer(plain, weights, followup=followup_due)
    assert score_due > score_plain
    assert "Follow-up due today" in reasons_due


def test_overdue_followup_outranks_due_followup():
    weights = dict(priority.DEFAULT_WEIGHTS)
    plain = make_customer_row()
    due = {"status": "DUE", "scheduled_at": datetime.now(timezone.utc).isoformat()}
    overdue = {"status": "OVERDUE", "scheduled_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}
    score_due, _ = priority.score_customer(plain, weights, followup=due)
    score_overdue, reasons_overdue = priority.score_customer(plain, weights, followup=overdue)
    assert score_overdue > score_due
    assert "Follow-up is overdue" in reasons_overdue


def test_broken_ptp_overdue_outranks_open_ptp():
    weights = dict(priority.DEFAULT_WEIGHTS)
    plain = make_customer_row()
    open_ptp = {"status": "PENDING", "promised_date": date.today().isoformat()}
    overdue_ptp = {"status": "PENDING", "promised_date": (date.today() - timedelta(days=5)).isoformat()}
    score_open, _ = priority.score_customer(plain, weights, ptp=open_ptp)
    score_overdue, reasons = priority.score_customer(plain, weights, ptp=overdue_ptp)
    assert score_overdue > score_open
    assert "Promise to pay is overdue" in reasons


def test_pending_future_followup_is_not_eligible_via_normalized_table():
    weights = dict(priority.DEFAULT_WEIGHTS)
    row = make_customer_row()
    future_followup = {"status": "PENDING", "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
    assert priority.is_eligible(row, weights, followup=future_followup) is False


def test_due_followup_status_is_always_eligible_regardless_of_schedule():
    weights = dict(priority.DEFAULT_WEIGHTS)
    row = make_customer_row()
    due_followup = {"status": "DUE", "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    assert priority.is_eligible(row, weights, followup=due_followup) is True


# ---------------------------------------------------------------------------
# Endpoint tests using a fake Supabase client + dependency override.
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeRpc:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeQuery:
    def __init__(self, rows, table_name=None, store=None):
        self._rows = list(rows)
        self._table_name = table_name
        self._store = store

    def select(self, *_a, **_k):
        return self

    def eq(self, key, value):
        self._rows = [row for row in self._rows if row.get(key) == value]
        return self

    def in_(self, key, values):
        self._rows = [row for row in self._rows if row.get(key) in values]
        return self

    def like(self, key, pattern):
        prefix = pattern.rstrip("%")
        self._rows = [row for row in self._rows if str(row.get(key) or "").startswith(prefix)]
        return self

    def order(self, *_a, desc=False, **_k):
        return self

    def limit(self, _n):
        return self

    def insert(self, payload):
        row = dict(payload)
        row.setdefault("id", f"{self._table_name}-new")
        row.setdefault("version", 1)
        row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        if self._store is not None:
            self._store.append(row)
        return FakeQuery([row], self._table_name, self._store)

    def update(self, payload):
        for row in self._rows:
            row.update(payload)
        return self

    def delete(self):
        return self

    def execute(self):
        return FakeResult(list(self._rows))


class FakeTable:
    def __init__(self, rows, name, store):
        self._rows = rows
        self._name = name
        self._store = store

    def select(self, *_a, **_k):
        return FakeQuery(self._rows, self._name, self._store)

    def insert(self, payload):
        return FakeQuery(self._rows, self._name, self._store).insert(payload)

    def update(self, payload):
        return FakeQuery(self._rows, self._name, self._store).update(payload)


class FakeSupabase:
    def __init__(self, customers=None, agents=None, campaigns=None, follow_ups=None, ptps=None, audit=None):
        self.tables = {
            "customers": customers or [], "agents": agents or [], "campaigns": campaigns or [],
            "follow_ups": follow_ups or [], "promise_to_pay": ptps or [], "queue_audit_log": audit or [],
            "queue_priority_weights": [],
        }
        self.rpc_calls = []

    def table(self, name):
        return FakeTable(self.tables.setdefault(name, []), name, self.tables.setdefault(name, []))

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if name in {"refresh_followup_states", "release_expired_locks", "expire_stale_ptps"}:
            return FakeRpc(FakeResult(0))
        if name == "create_callback_followup":
            new_id = "new-followup-1"
            self.tables["follow_ups"].append({
                "id": new_id, "campaign_id": params["p_campaign_id"], "customer_id": params["p_customer_id"],
                "agent_id": params["p_agent_id"], "type": "CALLBACK", "reason": params["p_reason"], "notes": params["p_notes"],
                "scheduled_at": params["p_scheduled_at"], "status": "PENDING", "version": 1, "ptp_id": None,
                "agent": {"name": "Mary"}, "campaign": {"name": "DD1-DD7"},
            })
            return FakeRpc(FakeResult(new_id))
        if name == "complete_follow_up":
            for row in self.tables["follow_ups"]:
                if row["id"] == params["p_follow_up_id"] and row.get("version") == params["p_version"] and row.get("status") not in {"COMPLETED", "CANCELLED"}:
                    row["status"] = "COMPLETED"
                    row["version"] += 1
                    row["completed_outcome"] = params["p_outcome"]
                    return FakeRpc(FakeResult(True))
            return FakeRpc(FakeResult(False))
        if name == "reschedule_follow_up":
            for row in self.tables["follow_ups"]:
                if row["id"] == params["p_follow_up_id"] and row.get("version") == params["p_version"] and row.get("status") not in {"COMPLETED", "CANCELLED"}:
                    row["status"] = "RESCHEDULED"
                    row["version"] += 1
                    new_id = "rescheduled-1"
                    self.tables["follow_ups"].append({**row, "id": new_id, "status": "PENDING", "scheduled_at": params["p_new_scheduled_at"], "version": 1, "rescheduled_from": row["id"]})
                    return FakeRpc(FakeResult(new_id))
            return FakeRpc(FakeResult(None))
        if name == "cancel_follow_up":
            for row in self.tables["follow_ups"]:
                if row["id"] == params["p_follow_up_id"] and row.get("version") == params["p_version"] and row.get("status") not in {"COMPLETED", "CANCELLED"}:
                    row["status"] = "CANCELLED"
                    row["version"] += 1
                    return FakeRpc(FakeResult(True))
            return FakeRpc(FakeResult(False))
        if name == "reassign_follow_up":
            for row in self.tables["follow_ups"]:
                if row["id"] == params["p_follow_up_id"] and row.get("version") == params["p_version"] and row.get("status") not in {"COMPLETED", "CANCELLED"}:
                    row["agent_id"] = params["p_new_agent_id"]
                    row["version"] += 1
                    return FakeRpc(FakeResult(True))
            return FakeRpc(FakeResult(False))
        if name == "create_ptp":
            new_ptp_id = "new-ptp-1"
            new_followup_id = "new-ptp-followup-1" if params.get("p_create_followup") else None
            self.tables["promise_to_pay"].append({
                "id": new_ptp_id, "campaign_id": params["p_campaign_id"], "customer_id": params["p_customer_id"],
                "agent_id": params["p_agent_id"], "promised_amount": params["p_promised_amount"], "promised_date": str(params["p_promised_date"]),
                "status": "PENDING", "paid_amount": 0, "remaining_amount": params["p_promised_amount"], "version": 1,
                "follow_up_id": new_followup_id, "agent": {"name": "Mary"}, "campaign": {"name": "DD1-DD7"},
            })
            if new_followup_id:
                self.tables["follow_ups"].append({
                    "id": new_followup_id, "campaign_id": params["p_campaign_id"], "customer_id": params["p_customer_id"],
                    "agent_id": params["p_agent_id"], "type": "PTP", "status": "PENDING", "version": 1, "ptp_id": new_ptp_id,
                    "scheduled_at": str(params["p_promised_date"]), "agent": {"name": "Mary"}, "campaign": {"name": "DD1-DD7"},
                })
            return FakeRpc(FakeResult([{"ptp_id": new_ptp_id, "follow_up_id": new_followup_id}]))
        if name == "update_ptp_status":
            for row in self.tables["promise_to_pay"]:
                if row["id"] == params["p_ptp_id"] and row.get("version") == params["p_version"] and row.get("status") not in {"FULFILLED", "CANCELLED", "EXPIRED"}:
                    row["status"] = params["p_status"]
                    if params.get("p_paid_amount") is not None:
                        row["paid_amount"] = params["p_paid_amount"]
                        row["remaining_amount"] = max(row["promised_amount"] - params["p_paid_amount"], 0)
                    row["version"] += 1
                    return FakeRpc(FakeResult(True))
            return FakeRpc(FakeResult(False))
        raise AssertionError(f"Unexpected RPC call: {name}")


AGENT_ID = "agent-123"
OTHER_AGENT_ID = "agent-999"


def make_customer(**overrides):
    row = {
        "campaign_id": "camp-1", "customer_id": "1001", "name": "Jane Wanjiku", "phone": "0700000000",
        "branch": "Nairobi", "sector": "Retail", "worked": False, "balance": 18400, "due_date": None,
        "pair": "", "disb_amount": None, "total_paid": 0, "status": "", "outcome": "", "business_status": "",
        "ptp_amount": None, "ptp_time": "", "feedback": "", "days_inactive": None, "days_dormant": None,
        "loyalty": "", "last_loan_amount": None, "source_data": {}, "updated_at": "", "attempts": 0,
        "last_contact_at": None, "follow_up_at": None, "assigned_agent_id": AGENT_ID, "assigned_agent": {"name": "Test Agent"},
        "campaign": {"name": "DD1-DD7", "priority": "high", "type": "defaulted"},
    }
    row.update(overrides)
    return row


def override_agent(role="Control Agent", agent_id=AGENT_ID):
    def _get_current_user():
        return {"id": agent_id, "name": "Test Agent", "email": "test.agent@4g-capital.com", "role": role, "status": "Active"}
    return _get_current_user


def install_fake_db(monkeypatch, fake):
    monkeypatch.setattr("api.index._supabase", fake, raising=False)
    monkeypatch.setattr("api.index.get_supabase", lambda: fake)


@pytest.fixture(autouse=True)
def _cleanup():
    priority.invalidate_weights_cache()
    yield
    app.dependency_overrides.clear()
    priority.invalidate_weights_cache()


def test_create_callback_followup(monkeypatch):
    fake = FakeSupabase(customers=[make_customer()], campaigns=[{"id": "camp-1", "name": "DD1-DD7"}])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.post("/api/followups", json={"customerId": "1001", "campaignName": "DD1-DD7", "type": "CALLBACK", "scheduledAt": "2026-09-10T10:00:00", "reason": "Customer requested callback"})
    assert response.status_code == 200
    assert fake.tables["follow_ups"][0]["type"] == "CALLBACK"


def test_create_ptp_followup(monkeypatch):
    fake = FakeSupabase(customers=[make_customer(balance=20000)], campaigns=[{"id": "camp-1", "name": "DD1-DD7"}])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.post("/api/ptp", json={"customerId": "1001", "campaignName": "DD1-DD7", "promisedAmount": 5000, "promisedDate": "2026-09-10", "createFollowup": True})
    assert response.status_code == 200
    body = response.json()
    assert body["ptpId"] == "new-ptp-1"
    assert body["followUpId"] == "new-ptp-followup-1"
    assert fake.tables["promise_to_pay"][0]["promised_amount"] == 5000


def test_ptp_validates_amount():
    fake = FakeSupabase(customers=[make_customer(balance=1000)])
    result = followups_module.validate_ptp_amount(-5, 1000, True)
    assert result["ok"] is False


def test_due_followup_appears_in_today_queue(monkeypatch):
    fake = FakeSupabase(follow_ups=[make_followup(id="f1", agent_id=AGENT_ID, status="DUE")])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.get("/api/followups?scope=today")
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "f1"


def test_overdue_followup_appears_in_overdue_list(monkeypatch):
    fake = FakeSupabase(follow_ups=[make_followup(id="f1", agent_id=AGENT_ID, status="OVERDUE")])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.get("/api/followups?scope=overdue")
    assert response.json()["items"][0]["id"] == "f1"


def test_followup_can_be_completed(monkeypatch):
    fake = FakeSupabase(follow_ups=[make_followup(id="f1", agent_id=AGENT_ID, status="DUE", version=1)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.post("/api/followups/f1/complete", json={"version": 1, "outcome": "Unable to reach customer", "notes": ""})
    assert response.status_code == 200
    assert fake.tables["follow_ups"][0]["status"] == "COMPLETED"


def test_followup_can_be_rescheduled(monkeypatch):
    fake = FakeSupabase(follow_ups=[make_followup(id="f1", agent_id=AGENT_ID, status="DUE", version=1)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.post("/api/followups/f1/reschedule", json={"version": 1, "newScheduledAt": "2026-09-15T10:00:00", "reason": "Customer requested Monday"})
    assert response.status_code == 200
    statuses = {row["id"]: row["status"] for row in fake.tables["follow_ups"]}
    assert statuses["f1"] == "RESCHEDULED"
    assert "rescheduled-1" in statuses


def test_followup_can_be_cancelled(monkeypatch):
    fake = FakeSupabase(follow_ups=[make_followup(id="f1", agent_id=AGENT_ID, status="DUE", version=1)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.post("/api/followups/f1/cancel", json={"version": 1, "reason": "Duplicate"})
    assert response.status_code == 200
    assert fake.tables["follow_ups"][0]["status"] == "CANCELLED"


def test_unauthorized_agent_cannot_modify_followup(monkeypatch):
    fake = FakeSupabase(follow_ups=[make_followup(id="f1", agent_id=OTHER_AGENT_ID, status="DUE", version=1)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.post("/api/followups/f1/complete", json={"version": 1, "outcome": "Payment received", "notes": ""})
    assert response.status_code == 403


def test_mark_fulfilled(monkeypatch):
    fake = FakeSupabase(ptps=[make_ptp(id="p1", agent_id=AGENT_ID, status="PENDING", version=1, promised_amount=5000)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.post("/api/ptp/p1/update-status", json={"version": 1, "status": "FULFILLED", "paidAmount": 5000})
    assert response.status_code == 200
    assert fake.tables["promise_to_pay"][0]["status"] == "FULFILLED"


def test_mark_partially_paid(monkeypatch):
    fake = FakeSupabase(ptps=[make_ptp(id="p1", agent_id=AGENT_ID, status="PENDING", version=1, promised_amount=5000)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.post("/api/ptp/p1/update-status", json={"version": 1, "status": "PARTIALLY_PAID", "paidAmount": 2000})
    assert response.status_code == 200
    row = fake.tables["promise_to_pay"][0]
    assert row["status"] == "PARTIALLY_PAID"
    assert row["remaining_amount"] == 3000


def test_mark_broken(monkeypatch):
    fake = FakeSupabase(ptps=[make_ptp(id="p1", agent_id=AGENT_ID, status="PENDING", version=1)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.post("/api/ptp/p1/update-status", json={"version": 1, "status": "BROKEN"})
    assert response.status_code == 200
    assert fake.tables["promise_to_pay"][0]["status"] == "BROKEN"


def test_ptp_dashboard_calculates_fulfillment_rate(monkeypatch):
    fake = FakeSupabase(ptps=[
        make_ptp(id="a", status="FULFILLED", promised_amount=5000, paid_amount=5000, remaining_amount=0),
        make_ptp(id="b", status="BROKEN", promised_amount=5000, paid_amount=0, remaining_amount=5000),
    ])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent(role="Team Leader", agent_id="tl-1")
    client = TestClient(app)

    response = client.get("/api/ptp/dashboard")
    assert response.status_code == 200
    assert response.json()["metrics"]["successRate"] == 50.0


def test_agent_cannot_view_ptp_dashboard(monkeypatch):
    fake = FakeSupabase()
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.get("/api/ptp/dashboard")
    assert response.status_code == 403


def test_customer_360_shows_current_followup_and_ptp(monkeypatch):
    fake = FakeSupabase(
        customers=[make_customer()],
        follow_ups=[make_followup(id="f1", agent_id=AGENT_ID, status="DUE", ptp_id="p1")],
        ptps=[make_ptp(id="p1", agent_id=AGENT_ID, status="PENDING")],
    )
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    body = client.get("/api/customers/1001/360").json()
    assert body["currentFollowUp"]["id"] == "f1"
    assert body["currentPtp"]["id"] == "p1"
    assert body["ptp_history"]["history"][0]["id"] == "p1"


def test_reassignment_is_auditable(monkeypatch):
    fake = FakeSupabase(
        follow_ups=[make_followup(id="f1", agent_id=AGENT_ID, status="DUE", version=1)],
        agents=[{"id": OTHER_AGENT_ID, "name": "Peter"}],
    )
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent(role="Team Leader", agent_id="tl-1")
    client = TestClient(app)

    response = client.post("/api/followups/f1/reassign", json={"version": 1, "newAgentName": "Peter", "reason": "Agent unavailable"})
    assert response.status_code == 200
    assert fake.tables["follow_ups"][0]["agent_id"] == OTHER_AGENT_ID


def test_two_agents_cannot_corrupt_same_followup_state(monkeypatch):
    fake = FakeSupabase(follow_ups=[make_followup(id="f1", agent_id=AGENT_ID, status="DUE", version=1)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    first = client.post("/api/followups/f1/complete", json={"version": 1, "outcome": "Payment received", "notes": ""})
    second = client.post("/api/followups/f1/complete", json={"version": 1, "outcome": "Promise broken", "notes": ""})
    assert first.status_code == 200
    assert second.status_code == 409
