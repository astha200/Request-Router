"""Routing policy behaviour. Pure functions - no network, no database."""
import pytest

from app.models import CHEAP, FRONTIER
from app.routing import THRESHOLD, decide

def msg(text): return [{"role": "user", "content": text}]

SIMPLE = [
    "Rename the variable `usr` to `user` in this function.",
    "Summarize what this function does:\n```python\ndef f(x): return x*2\n```",
    "Write a simple unit test for add(a, b).",
    "Explain what this short helper does.",
]
COMPLEX = [
    "Our checkout service returns 500 intermittently in production.\n"
    "Traceback (most recent call last):\n  File 'pay.py', line 88\n    ValueError: token expired\n"
    "Only happens under load. Why is this failing?",
    "Do a security review of this auth middleware for SQL injection and CSRF.",
    "Should we use event sourcing or CRUD? Walk me through the architecture tradeoffs at scale.",
    "There's a race condition between two goroutines writing the same map. How do I fix it safely?",
]

@pytest.mark.parametrize("text", SIMPLE)
def test_simple_requests_route_cheap(text):
    d = decide(msg(text))
    assert d.model is CHEAP, f"expected cheap, got {d.model.id} (score {d.score})"
    assert d.mode == "routed"

@pytest.mark.parametrize("text", COMPLEX)
def test_complex_requests_route_frontier(text):
    d = decide(msg(text))
    assert d.model is FRONTIER, f"expected frontier, got {d.model.id} (score {d.score})"
    assert d.mode == "routed"

def test_every_decision_has_a_human_readable_reason():
    for text in SIMPLE + COMPLEX:
        d = decide(msg(text))
        assert d.reason and len(d.reason) > 30
        assert d.reason.startswith(("Cheap:", "Frontier:"))
        assert "score" in d.reason  # decision is auditable

def test_reason_names_the_signal_that_drove_it():
    d = decide(msg(COMPLEX[1]))
    assert "security" in d.reason.lower()
    assert any(s.name == "security" for s in d.signals)

def test_brevity_does_not_downgrade_a_serious_question():
    """A terse security question is still a security question."""
    d = decide(msg("Security review: is this vulnerable to XSS?"))
    assert d.model is FRONTIER
    assert not any(s.weight < 0 for s in d.signals)

def test_route_hint_overrides_policy_and_is_labelled():
    d = decide(msg("rename x to y"), route_hint="frontier")
    assert d.model is FRONTIER and d.mode == "routed_with_override"
    assert "X-Route-Hint" in d.reason

def test_score_threshold_is_respected():
    for text in SIMPLE:
        assert decide(msg(text)).score < THRESHOLD
    for text in COMPLEX:
        assert decide(msg(text)).score >= THRESHOLD

def test_handles_structured_content_parts():
    """OpenAI content can be a list of typed parts, not just a string."""
    d = decide([{"role": "user", "content": [
        {"type": "text", "text": "security review of this auth token handler"}]}])
    assert d.model is FRONTIER

def test_handles_empty_and_malformed_messages():
    assert decide([]).model is CHEAP
    assert decide([{"role": "user"}]).model is CHEAP


# --- regressions: phrasings that slipped through earlier versions -------------

@pytest.mark.parametrize("text", [
    "Production traceback ValueError why failing under load?",
    "Why is this failing intermittently?",
    "We had an outage last night, investigate the root cause.",
    "Why does the worker crash in prod?",
    "There were two incidents this week with the same regression.",
])
def test_natural_debugging_phrasings_reach_frontier(text):
    """Word-suffix bugs (`intermittently`, `outages`, `failing`) used to hide these."""
    assert decide(msg(text)).model is FRONTIER, f"scored {decide(msg(text)).score}"


def test_reason_code_is_stable_and_groupable():
    assert decide(msg(SIMPLE[0])).reason_code == "routine_edit"
    assert decide(msg(COMPLEX[1])).reason_code == "security"
    assert decide(msg("rename x"), route_hint="frontier").reason_code == "route_hint_override"


def test_reason_code_distinguishes_below_threshold_from_a_dampener():
    """'Escalation signals fired but fell short' is a different story from
    'this looked routine', and the label has to say which."""
    # "production" fires (+3.0) but does not reach 4.0 -> not the same as routine.
    below = decide(msg("Add a docstring to our production config parser."))
    assert below.reason_code == "below_threshold"
    assert below.score > 0

    # Nothing escalating; a routine-work signal is what explains this one.
    routine = decide(msg("Rename the variable usr to user in this function."))
    assert routine.reason_code == "routine_edit"
    assert routine.score < 0


def test_every_decision_carries_a_policy_version():
    from app.models import POLICY_VERSION
    assert decide(msg(SIMPLE[0])).policy_version == POLICY_VERSION
