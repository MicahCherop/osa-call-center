"""Tests for the Customer 360 view.

Run with: pytest tests/test_customer_360.py -v
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import customer360  # noqa: E402
from api.index import app, get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Pure shaping unit tests (api/customer360.py) - no mocking required.
# ---------------------------------------------------------------------------

def make_disposition(**overrides):
    row = {
        "id": "d1", "customer_id": "1001", "campaign_id": "camp-1", "agent_id": "agent-1",
        "outcome": "Answered", "status": "", "amount_rec": 0, "comments": "", "business_status": "",
        "ptp_time": "", "follow_up_at": None, "created_at": datetime.now(timezone.utc).isoformat(),
        "agent": {"name": "Mary"},
    }
    row.update(overrides)
    return row


def test_timeline_is_ordered_newest_first():
    older = make_disposition(id="d1", created_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
    newer = make_disposition(id="d2", created_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    timeline = customer360.build_timeline([older, newer], [], [])
    assert timeline[0]["timestamp"] == newer["created_at"]
    assert timeline[-1]["timestamp"] == older["created_at"]


def test_contactability_counts_are_correct():
    dispositions = [
        make_disposition(outcome="Answered"),
        make_disposition(outcome="Answered"),
        make_disposition(outcome="Not Answered"),
        make_disposition(outcome="Number Busy"),
    ]
    stats = customer360.build_contactability(dispositions)
    assert stats["attempts"] == 4
    assert stats["answered"] == 2
    assert stats["breakdown"]["Not Answered"] == 1
    assert stats["contactRate"] == 50.0


def test_ptp_history_calculates_totals():
    dispositions = [
        make_disposition(status="Promise to Pay (PTP)", amount_rec=5000),
        make_disposition(status="Promise to Pay (PTP)", amount_rec=10000),
        make_disposition(status="", amount_rec=0),
    ]
    result = customer360.build_ptp_history(dispositions, {"total_paid": 5000})
    assert result["totalPromised"] == 15000
    assert result["paid"] == 5000
    assert result["outstanding"] == 10000
    assert len(result["history"]) == 2


def test_followup_history_appears_correctly():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    dispositions = [make_disposition(follow_up_at=future)]
    followups = customer360.build_followups(dispositions)
    assert len(followups) == 1
    assert followups[0]["state"] == "Pending"


def test_missing_history_produces_empty_state_friendly_result():
    assert customer360.build_contact_history([]) == []
    ptp = customer360.build_ptp_history([], {"total_paid": 0})
    assert ptp["history"] == []
    assert ptp["totalPromised"] == 0


def test_pagination_works():
    items = list(range(25))
    page1, total, has_more = customer360.paginate(items, page=1, page_size=10)
    assert page1 == list(range(10))
    assert total == 25
    assert has_more is True
    page3, _, has_more_last = customer360.paginate(items, page=3, page_size=10)
    assert page3 == list(range(20, 25))
    assert has_more_last is False


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
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *_a, **_k):
        return self

    def eq(self, key, value):
        self._rows = [row for row in self._rows if row.get(key) == value]
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def insert(self, payload):
        row = dict(payload)
        row.setdefault("id", "note-new")
        row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        row.setdefault("updated_at", row["created_at"])
        self._rows.append(row)
        return FakeQuery([row])

    def upsert(self, payload, on_conflict=None):
        return self.insert(payload)

    def update(self, payload):
        for row in self._rows:
            row.update(payload)
        return self

    def delete(self):
        return self

    def execute(self):
        return FakeResult(list(self._rows))


class FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return FakeQuery(self._rows)

    def insert(self, payload):
        return FakeQuery(self._rows).insert(payload)

    def upsert(self, payload, on_conflict=None):
        return FakeQuery(self._rows).upsert(payload, on_conflict)

    def update(self, payload):
        return FakeQuery(self._rows).update(payload)

    def delete(self):
        return FakeQuery(self._rows).delete()


class FakeSupabase:
    def __init__(self, customers=None, campaigns=None, dispositions=None, notes=None, tags=None, audit=None):
        self.tables = {
            "customers": customers or [],
            "campaigns": campaigns or [],
            "dispositions": dispositions or [],
            "customer_notes": notes or [],
            "customer_tags": tags or [],
            "customer_tag_definitions": [],
            "queue_audit_log": audit or [],
        }

    def table(self, name):
        return FakeTable(self.tables.setdefault(name, []))

    def rpc(self, name, params):
        return FakeRpc(FakeResult(True))


AGENT_ID = "agent-123"
OTHER_AGENT_ID = "agent-999"


def make_customer(**overrides):
    row = {
        "campaign_id": "camp-1", "customer_id": "1001", "name": "Jane Wanjiku", "phone": "0700000000",
        "branch": "Nairobi", "sector": "Retail", "worked": False, "balance": 18400, "due_date": None,
        "pair": "", "disb_amount": None, "total_paid": 0, "status": "", "outcome": "", "business_status": "",
        "ptp_amount": None, "ptp_time": "", "feedback": "", "days_inactive": None, "days_dormant": None,
        "loyalty": "", "last_loan_amount": None, "source_data": {}, "updated_at": "", "attempts": 0,
        "last_contact_at": None, "follow_up_at": None, "source_url": "", "disb_date": "", "loan_code": "",
        "dd_days": None, "account_status": "", "bfc_blc": "", "number_of_loans": None, "risk_band": "",
        "increment_status": "", "affordability": None, "loan_limit": None, "interest": None, "total_due": None,
        "penalty": None, "assigned_agent_id": AGENT_ID, "assigned_agent": {"name": "Test Agent"},
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
    yield
    app.dependency_overrides.clear()


def test_authorized_agent_can_view_assigned_customer(monkeypatch):
    fake = FakeSupabase(customers=[make_customer(assigned_agent_id=AGENT_ID)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.get("/api/customers/1001/360")
    assert response.status_code == 200
    body = response.json()
    assert body["customer"]["id"] == "1001"
    assert body["summary"]["outstandingBalance"] == 18400


def test_unauthorized_agent_cannot_view_another_agents_customer(monkeypatch):
    fake = FakeSupabase(customers=[make_customer(assigned_agent_id=OTHER_AGENT_ID)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.get("/api/customers/1001/360")
    assert response.status_code == 403


def test_team_leader_can_view_permitted_customers(monkeypatch):
    fake = FakeSupabase(customers=[make_customer(assigned_agent_id=OTHER_AGENT_ID)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent(role="Team Leader", agent_id="tl-1")
    client = TestClient(app)

    response = client.get("/api/customers/1001/360")
    assert response.status_code == 200


def test_customer_not_found_returns_friendly_404(monkeypatch):
    fake = FakeSupabase(customers=[])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.get("/api/customers/9999/360")
    assert response.status_code == 404
    assert "database" not in response.json()["detail"].lower()


def test_empty_history_shows_friendly_empty_state(monkeypatch):
    fake = FakeSupabase(customers=[make_customer()])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    body = client.get("/api/customers/1001/360").json()
    assert body["contact_history"]["items"] == []
    assert body["ptp_history"]["history"] == []
    assert body["notes"] == []


def test_disposition_appears_immediately_after_saving(monkeypatch):
    customer = make_customer()
    fake = FakeSupabase(customers=[customer])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    fake.tables["dispositions"].append({
        "id": "d1", "customer_id": "1001", "campaign_id": "camp-1", "agent_id": AGENT_ID,
        "outcome": "Answered", "status": "Promise to Pay (PTP)", "amount_rec": 5000, "comments": "Will pay Friday",
        "business_status": "", "ptp_time": "Friday", "follow_up_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(), "agent": {"name": "Mary"},
    })
    body = client.get("/api/customers/1001/360").json()
    assert body["contact_history"]["items"][0]["outcome"] == "Answered"
    assert body["summary"]["totalCalls"] == 1


def test_pagination_works_on_timeline_endpoint(monkeypatch):
    customer = make_customer()
    fake = FakeSupabase(customers=[customer])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)
    for index in range(5):
        fake.tables["dispositions"].append({
            "id": f"d{index}", "customer_id": "1001", "campaign_id": "camp-1", "agent_id": AGENT_ID,
            "outcome": "Answered", "status": "", "amount_rec": 0, "comments": "", "business_status": "",
            "ptp_time": "", "follow_up_at": None,
            "created_at": (datetime.now(timezone.utc) - timedelta(days=index)).isoformat(), "agent": {"name": "Mary"},
        })
    response = client.get("/api/customers/1001/timeline?page=1&pageSize=2")
    body = response.json()
    assert len(body["items"]) == 2
    assert body["hasMore"] is True
    assert body["total"] == 5


def test_sensitive_lock_fields_are_not_exposed_to_agent(monkeypatch):
    fake = FakeSupabase(customers=[make_customer(locked_by="someone", lock_expires_at="2026-01-01")])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    body = client.get("/api/customers/1001/360").json()
    assert "locked_by" not in body["customer"]
    assert "lock_expires_at" not in body["customer"]
    assert "lockedBy" not in body["customer"]
