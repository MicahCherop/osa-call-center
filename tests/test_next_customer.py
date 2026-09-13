"""Tests for the Intelligent Next Customer queue.

Run with: pytest tests/test_next_customer.py -v
(from the repository root, with the api/ dependencies installed).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import priority  # noqa: E402
from api.index import app, get_current_user, get_supabase  # noqa: E402


# ---------------------------------------------------------------------------
# Pure scoring/eligibility unit tests (api/priority.py) - no mocking required.
# ---------------------------------------------------------------------------

WEIGHTS = dict(priority.DEFAULT_WEIGHTS)


def make_row(**overrides):
    row = {
        "campaign_id": "camp-1", "customer_id": "1001", "name": "Jane Doe", "phone": "0700000000",
        "branch": "Nairobi", "sector": "Retail", "worked": False,
        "balance": 1000, "due_date": None, "pair": "", "disb_amount": None, "total_paid": None,
        "status": "", "outcome": "", "business_status": "", "ptp_amount": None, "ptp_time": "",
        "feedback": "", "days_inactive": None, "days_dormant": None, "loyalty": "", "last_loan_amount": None,
        "attempts": 0, "last_contact_at": None, "follow_up_at": None, "locked_by": None, "lock_expires_at": None,
        "source_data": {}, "updated_at": "", "assigned_agent_id": None, "assigned_agent": {},
        "campaign": {"name": "Test Campaign", "priority": "medium"},
    }
    row.update(overrides)
    return row


def test_highest_priority_customer_is_selected():
    low = make_row(customer_id="low", campaign={"name": "c", "priority": "low"}, balance=0)
    high = make_row(customer_id="high", campaign={"name": "c", "priority": "high"}, balance=90000)
    ranked = priority.rank_candidates([low, high], WEIGHTS)
    assert ranked[0][2]["customer_id"] == "high"


def test_followup_due_customer_receives_priority_boost():
    no_followup = make_row(customer_id="a")
    followup_due = make_row(customer_id="b", follow_up_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())
    score_a, _ = priority.score_customer(no_followup, WEIGHTS)
    score_b, reasons_b = priority.score_customer(followup_due, WEIGHTS)
    assert score_b > score_a
    assert "Follow-up due today" in reasons_b


def test_ptp_customer_receives_priority_boost():
    plain = make_row(customer_id="a", status="")
    ptp = make_row(customer_id="b", status="Promise to Pay (PTP)")
    score_a, _ = priority.score_customer(plain, WEIGHTS)
    score_b, reasons_b = priority.score_customer(ptp, WEIGHTS)
    assert score_b > score_a
    assert "Outstanding promise to pay" in reasons_b


def test_recently_contacted_customer_receives_penalty():
    stale = make_row(customer_id="a", last_contact_at=(datetime.now(timezone.utc) - timedelta(hours=48)).isoformat())
    recent = make_row(customer_id="b", last_contact_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
    score_stale, _ = priority.score_customer(stale, WEIGHTS)
    score_recent, _ = priority.score_customer(recent, WEIGHTS)
    assert score_recent < score_stale


def test_locked_customer_is_not_eligible():
    locked = make_row(lock_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    assert priority.is_eligible(locked, WEIGHTS) is False


def test_expired_lock_becomes_eligible_again():
    expired_lock = make_row(lock_expires_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())
    assert priority.is_eligible(expired_lock, WEIGHTS) is True


def test_completed_customer_is_not_eligible():
    completed = make_row(worked=True)
    assert priority.is_eligible(completed, WEIGHTS) is False


def test_future_followup_customer_is_not_eligible():
    future_followup = make_row(follow_up_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    assert priority.is_eligible(future_followup, WEIGHTS) is False


def test_customer_over_max_attempts_is_not_eligible():
    weights = dict(WEIGHTS)
    weights["max_attempts"] = 2
    over_limit = make_row(attempts=2)
    assert priority.is_eligible(over_limit, weights) is False


# ---------------------------------------------------------------------------
# Endpoint tests (api/index.py) using a fake Supabase client + dependency override.
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self._rows = [row for row in self._rows if row.get(key) == value]
        return self

    def in_(self, key, values):
        self._rows = [row for row in self._rows if row.get(key) in values]
        return self

    def limit(self, _n):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        return FakeResult(list(self._rows))


class FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_args, **_kwargs):
        return FakeQuery(self._rows)


class FakeRpc:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeSupabase:
    """Minimal stand-in for the Supabase client used by the Next Customer endpoints."""

    def __init__(self, customers, agents=None, weights_row=None):
        self.customers = customers
        self.agents = agents or []
        self.weights_row = weights_row or {}
        self.locked_customer_ids = set()
        self.reserve_calls = []

    def table(self, name):
        if name == "customers":
            return FakeTable(self.customers)
        if name == "agents":
            return FakeTable(self.agents)
        if name == "queue_priority_weights":
            return FakeTable([self.weights_row] if self.weights_row else [])
        if name == "campaigns":
            return FakeTable([])
        return FakeTable([])

    def rpc(self, name, params):
        if name in {"release_expired_locks", "refresh_followup_states", "expire_stale_ptps"}:
            return FakeRpc(FakeResult(0))
        if name == "reserve_customer_lock":
            self.reserve_calls.append(params)
            customer_id = params["p_customer_id"]
            if customer_id in self.locked_customer_ids:
                return FakeRpc(FakeResult(False))
            self.locked_customer_ids.add(customer_id)
            return FakeRpc(FakeResult(True))
        if name == "skip_customer":
            return FakeRpc(FakeResult(True))
        if name == "get_queue_summary":
            return FakeRpc(FakeResult([{"high_priority": 1, "follow_ups": 2, "ptp_customers": 3, "new_customers": 4, "total": 10}]))
        raise AssertionError(f"Unexpected RPC call: {name}")


AGENT_ID = "agent-123"
OTHER_AGENT_ID = "agent-999"


def override_agent(role="Control Agent"):
    def _get_current_user():
        return {"id": AGENT_ID, "name": "Test Agent", "email": "test.agent@4g-capital.com", "role": role, "status": "Active"}
    return _get_current_user


def install_fake_db(monkeypatch, fake):
    monkeypatch.setattr("api.index._supabase", fake, raising=False)
    monkeypatch.setattr("api.index.get_supabase", lambda: fake)


@pytest.fixture(autouse=True)
def _reset_weights_cache():
    priority.invalidate_weights_cache()
    yield
    priority.invalidate_weights_cache()
    app.dependency_overrides.clear()


def test_agent_receives_eligible_customer(monkeypatch):
    fake = FakeSupabase(customers=[make_row(customer_id="1001", assigned_agent_id=AGENT_ID)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.get("/api/agent/next-customer")
    assert response.status_code == 200
    body = response.json()
    assert body["customer"]["id"] == "1001"
    assert "priorityScore" in body


def test_agent_cannot_receive_another_agents_customer(monkeypatch):
    fake = FakeSupabase(customers=[make_row(customer_id="2001", assigned_agent_id=OTHER_AGENT_ID)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.get("/api/agent/next-customer")
    assert response.status_code == 200
    # The DB query itself filters by assigned_agent_id=AGENT_ID (see FakeTable.eq), so a customer
    # belonging to another agent never appears in the candidate set returned to this agent.
    assert response.json()["customer"] is None


def test_empty_queue_returns_friendly_response(monkeypatch):
    fake = FakeSupabase(customers=[])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    response = client.get("/api/agent/next-customer")
    assert response.status_code == 200
    body = response.json()
    assert body["customer"] is None
    assert "message" in body


def test_unauthorized_role_cannot_access_next_customer(monkeypatch):
    fake = FakeSupabase(customers=[make_row(customer_id="1001", assigned_agent_id=AGENT_ID)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent(role="Admin")
    client = TestClient(app)

    response = client.get("/api/agent/next-customer")
    assert response.status_code == 403


def test_two_simultaneous_requests_do_not_receive_the_same_customer(monkeypatch):
    fake = FakeSupabase(customers=[make_row(customer_id="1001", assigned_agent_id=AGENT_ID)])
    install_fake_db(monkeypatch, fake)
    app.dependency_overrides[get_current_user] = override_agent()
    client = TestClient(app)

    first = client.get("/api/agent/next-customer")
    second = client.get("/api/agent/next-customer")
    assert first.json()["customer"]["id"] == "1001"
    # Second request finds the same (now-locked) candidate but fails to reserve it, and there is
    # no other eligible candidate, so it correctly reports no customer available.
    assert second.json()["customer"] is None
    assert len(fake.reserve_calls) == 2
