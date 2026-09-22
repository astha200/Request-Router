"""Guard the routing policy against regressions using the labelled eval set.

The eval harness itself lives in eval/ and is meant to be read. This pins its
headline numbers so a policy change that quietly degrades routing fails CI
instead of shipping.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.cases import ALL_CASES, CASES, HARD_CASES  # noqa: E402
from eval.run_eval import evaluate  # noqa: E402

from app import routing  # noqa: E402


def test_eval_set_is_balanced():
    lanes = [c[1] for c in ALL_CASES]
    assert 0.4 < lanes.count("cheap") / len(lanes) < 0.6, "eval set should not be lane-skewed"
    assert len(HARD_CASES) >= 8, "keep adversarial cases in the set - they measure the ceiling"


def test_policy_is_perfect_on_the_core_set():
    """The straightforward cases are the contract; regressions here are bugs."""
    for prompt, expected, tag in CASES:
        d = routing.decide([{"role": "user", "content": prompt}])
        assert d.category == expected, f"[{tag}] {prompt[:60]!r} -> {d.category} (score {d.score})"


def test_overall_accuracy_does_not_regress():
    _, m = evaluate(routing.THRESHOLD)
    assert m["accuracy"] >= 0.84, f"accuracy regressed to {m['accuracy']:.1%}"
    assert m["recall"] >= 0.80, f"recall regressed to {m['recall']:.1%}"


def test_current_threshold_is_on_the_stable_plateau():
    """4.0 must remain the highest threshold that preserves recall."""
    _, at_current = evaluate(4.0)
    _, above = evaluate(4.5)
    assert at_current["recall"] > above["recall"], (
        "4.0 should sit at the top of the plateau; if raising it no longer costs "
        "recall, the signal weights have shifted and the threshold needs revisiting"
    )


@pytest.mark.parametrize("threshold", [3.0, 3.5, 4.0])
def test_policy_is_stable_across_the_plateau(threshold):
    """Small threshold changes must not swing behaviour wildly - that would mean
    the score is knife-edge rather than a real separation."""
    _, m = evaluate(threshold)
    assert m["accuracy"] >= 0.80
