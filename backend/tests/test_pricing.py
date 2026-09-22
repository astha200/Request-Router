"""Cost and savings arithmetic."""
import tempfile, os
import pytest

from app import store
from app.models import CHEAP, FRONTIER
from app.pricing import cost_usd, frontier_baseline_usd
from app.store import RequestRecord


def test_cost_uses_published_per_million_rates():
    # 1M input + 1M output at cheap rates == input_rate + output_rate exactly.
    assert cost_usd(CHEAP, 1_000_000, 1_000_000) == pytest.approx(
        CHEAP.input_per_1m + CHEAP.output_per_1m)
    assert cost_usd(FRONTIER, 1_000_000, 1_000_000) == pytest.approx(
        FRONTIER.input_per_1m + FRONTIER.output_per_1m)

def test_cost_worked_example():
    # 1000 in / 500 out on the cheap model.
    expected = (1000/1e6)*0.15 + (500/1e6)*0.50
    assert cost_usd(CHEAP, 1000, 500) == pytest.approx(expected)
    assert cost_usd(CHEAP, 1000, 500) == pytest.approx(0.0004)

def test_zero_tokens_is_zero_cost():
    assert cost_usd(CHEAP, 0, 0) == 0.0

def test_baseline_is_always_the_frontier_price():
    assert frontier_baseline_usd(1000, 500) == cost_usd(FRONTIER, 1000, 500)
    assert frontier_baseline_usd(1000, 500) == pytest.approx(0.0105)

def test_baseline_exceeds_cheap_cost_for_identical_tokens():
    assert frontier_baseline_usd(5000, 2000) > cost_usd(CHEAP, 5000, 2000)


@pytest.fixture()
def db():
    path = tempfile.mktemp(suffix=".db")
    store.init_db(path)
    yield path
    for suffix in ("", "-wal", "-shm"):
        try: os.unlink(path + suffix)
        except OSError: pass

def _rec(rid, category, mode, inp, out, spec):
    return RequestRecord(
        id=rid, created_at=f"2026-09-03T10:00:{rid[-2:]}Z", attribution_id="astha",
        requested_model="fireworks-router/auto", selected_model=spec.id,
        model_category=category, decision_mode=mode, route_reason="r",
        reason_code="test_code", policy_version="v1.1", route_signals=[],
        route_score=0.0, input_tokens=inp, output_tokens=out, tokens_estimated=False,
        cost_usd=cost_usd(spec, inp, out), frontier_baseline_usd=frontier_baseline_usd(inp, out),
        latency_ms=1000, streamed=False)

def test_savings_is_baseline_minus_actual_over_routed_requests(db):
    store.record(_rec("r01", "cheap", "routed", 1000, 500, CHEAP), db)
    store.record(_rec("r02", "frontier", "routed", 1000, 500, FRONTIER), db)
    s = store.summary(db_path=db)
    # baseline for both = 2 * 0.0105; actual = 0.0004 + 0.0105
    assert s["routed_frontier_baseline_usd"] == pytest.approx(0.021)
    assert s["routed_spend_usd"] == pytest.approx(0.0109)
    assert s["estimated_savings_usd"] == pytest.approx(0.0101)

def test_pinned_requests_are_excluded_from_savings(db):
    """A pin is the developer's decision, not the router's. We do not take credit."""
    store.record(_rec("r01", "cheap", "routed", 1000, 500, CHEAP), db)
    store.record(_rec("r02", "cheap", "pinned", 1000, 500, CHEAP), db)
    s = store.summary(db_path=db)
    assert s["pinned_count"] == 1
    # Only the routed request contributes to the baseline.
    assert s["routed_frontier_baseline_usd"] == pytest.approx(0.0105)
    assert s["estimated_savings_usd"] == pytest.approx(0.0101)
    # ...but pinned spend still reconciles into the total.
    assert s["total_spend_usd"] == pytest.approx(s["routed_spend_usd"] + s["pinned_spend_usd"])

def test_frontier_pin_never_shows_as_savings(db):
    store.record(_rec("r01", "frontier", "pinned", 1000, 500, FRONTIER), db)
    s = store.summary(db_path=db)
    assert s["estimated_savings_usd"] == 0.0
    assert s["savings_pct"] == 0.0
    assert s["total_spend_usd"] == pytest.approx(0.0105)

def test_route_mix_percentages(db):
    for i in range(3):
        store.record(_rec(f"r0{i}", "cheap", "routed", 100, 100, CHEAP), db)
    store.record(_rec("r09", "frontier", "routed", 100, 100, FRONTIER), db)
    s = store.summary(db_path=db)
    assert s["cheap_pct"] == 75.0 and s["frontier_pct"] == 25.0

def test_empty_store_does_not_divide_by_zero(db):
    s = store.summary(db_path=db)
    assert s["total_requests"] == 0 and s["savings_pct"] == 0.0 and s["cheap_pct"] == 0.0

def test_attribution_filtering_isolates_spend(db):
    a = _rec("r01", "cheap", "routed", 1000, 500, CHEAP)
    b = _rec("r02", "frontier", "routed", 1000, 500, FRONTIER); b.attribution_id = "priya"
    store.record(a, db); store.record(b, db)
    assert store.summary("astha", db_path=db)["total_spend_usd"] == pytest.approx(0.0004)
    assert store.summary("priya", db_path=db)["total_spend_usd"] == pytest.approx(0.0105)
    assert len(store.recent_requests("priya", db_path=db)) == 1
