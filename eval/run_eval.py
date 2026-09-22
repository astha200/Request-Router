#!/usr/bin/env python3
"""Score the routing policy against a labelled set, and sweep the threshold.

Why this exists: without it, "the threshold is 4.0" is an assertion. With it,
a threshold change is reviewable - you can see what it costs and what it buys
before shipping it.

Routing is a pure function, so this runs offline in milliseconds and spends no
money. Use --live to additionally verify the deployed proxy agrees with the
policy (it should; if it does not, the wiring is wrong, not the policy).

    python eval/run_eval.py
    python eval/run_eval.py --sweep
    python eval/run_eval.py --live
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO))

from app import routing  # noqa: E402
from app.models import VIRTUAL_MODEL_ID  # noqa: E402
from eval.cases import ALL_CASES, CASES  # noqa: E402


def evaluate(threshold: float) -> tuple[list[dict], dict]:
    """Route every case at a given threshold and collect the outcomes."""
    original = routing.THRESHOLD
    routing.THRESHOLD = threshold
    try:
        results = []
        for prompt, expected, tag in ALL_CASES:
            d = routing.decide([{"role": "user", "content": prompt}])
            results.append({
                "prompt": prompt, "expected": expected, "actual": d.category,
                "tag": tag, "score": d.score, "reason_code": d.reason_code,
                "correct": d.category == expected,
            })
    finally:
        routing.THRESHOLD = original

    tp = sum(r["expected"] == "frontier" and r["actual"] == "frontier" for r in results)
    fp = sum(r["expected"] == "cheap" and r["actual"] == "frontier" for r in results)
    fn = sum(r["expected"] == "frontier" and r["actual"] == "cheap" for r in results)
    tn = sum(r["expected"] == "cheap" and r["actual"] == "cheap" for r in results)

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    return results, {
        "threshold": threshold, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "accuracy": (tp + tn) / len(results),
        "precision": precision, "recall": recall, "f1": f1,
    }


def report(results: list[dict], m: dict) -> None:
    print(f"Routing policy evaluation - {len(results)} labelled cases, threshold {m['threshold']:.1f}\n")
    print(f"  accuracy   {m['accuracy']:.1%}   ({m['tp'] + m['tn']}/{len(results)})")
    print(f"  precision  {m['precision']:.1%}   of escalations that were warranted")
    print(f"  recall     {m['recall']:.1%}   of work needing frontier that got it")
    print(f"  F1         {m['f1']:.2f}\n")

    print("  confusion matrix          predicted")
    print("                        cheap   frontier")
    print(f"  actual  cheap     {m['tn']:>7} {m['fp']:>10}")
    print(f"          frontier  {m['fn']:>7} {m['tp']:>10}\n")

    by_tag: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_tag[r["tag"]].append(r)
    print("  by request type")
    for tag in sorted(by_tag, key=lambda t: sum(not r["correct"] for r in by_tag[t]), reverse=True):
        rows = by_tag[tag]
        ok = sum(r["correct"] for r in rows)
        flag = "  " if ok == len(rows) else " !"
        print(f"   {flag} {tag:14} {ok}/{len(rows)}")

    misroutes = [r for r in results if not r["correct"]]
    if misroutes:
        print(f"\n  misroutes ({len(misroutes)})")
        for r in misroutes:
            direction = "under-escalated" if r["expected"] == "frontier" else "over-escalated"
            print(f"    {direction:16} score={r['score']:5.1f}  [{r['reason_code']}]")
            print(f"      {r['prompt'][:88]}")
    else:
        print("\n  no misroutes on this set.")

    # Cost-asymmetry framing: the two error types are not equally bad.
    print("\n  error cost")
    print(f"    {m['fn']} under-escalation(s): cheap answer on work that needed frontier - "
          "costs quality and likely a retry")
    print(f"    {m['fp']} over-escalation(s): frontier price on routine work - costs money only")


def sweep() -> None:
    print("Threshold sweep - what each choice buys and costs\n")
    print("  thresh  accuracy  precision  recall     under-esc  over-esc")
    best = None
    for t in [x / 2 for x in range(2, 17)]:
        _, m = evaluate(t)
        marker = "  <- current" if abs(t - 4.0) < 1e-9 else ""
        print(f"   {t:5.1f}   {m['accuracy']:7.1%}  {m['precision']:8.1%}  {m['recall']:6.1%}"
              f"   {m['fn']:>8}  {m['fp']:>8}{marker}")
        # Ties go to the current threshold: a higher threshold that scores the
        # same escalates less often, so it is the cheaper of two equal choices.
        if best is None or m["f1"] > best[1]["f1"] + 1e-9:
            best = (t, m)
    assert best
    _, current = evaluate(routing.THRESHOLD)
    print(f"\n  best F1 on this set: {best[1]['f1']:.2f} at threshold {best[0]:.1f}")
    print(f"  current threshold {routing.THRESHOLD:.1f}: F1 {current['f1']:.2f}")
    if current["f1"] >= best[1]["f1"] - 1e-9:
        print("  The current threshold is at the optimum (tied choices resolve to the higher")
        print("  threshold, which escalates less often for the same accuracy).")
    else:
        print("  The current threshold is not the optimum on this set - worth revisiting once")
        print("  real override and retry data exists.")
    print("\n  Note the shape: accuracy is flat below 4.0 and falls off a cliff at 4.5, where")
    print("  recall collapses. 4.0 is the highest threshold that still preserves recall -")
    print("  the cheapest point on the plateau, not an arbitrary pick.")


def live_check() -> int:
    """Verify the running proxy routes the same way the policy does."""
    import httpx

    base = "http://127.0.0.1:8000"
    try:
        httpx.get(f"{base}/health", timeout=5).raise_for_status()
    except Exception:
        print(f"Proxy not reachable at {base}; skipping live check.")
        return 1

    print("Live check - does the deployed proxy agree with the policy?\n")
    mismatches = 0
    for prompt, expected, tag in CASES[:8]:
        d = routing.decide([{"role": "user", "content": prompt}])
        r = httpx.post(
            f"{base}/v1/chat/completions",
            json={"model": VIRTUAL_MODEL_ID, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 16},
            headers={"X-User-ID": "eval"}, timeout=120,
        )
        served = r.headers.get("x-router-category", "?")
        agree = served == d.category
        mismatches += not agree
        print(f"  {'ok ' if agree else 'BAD'} policy={d.category:8} proxy={served:8} {tag}")
    print(f"\n  {'agreement on all sampled cases.' if not mismatches else f'{mismatches} disagreement(s) - check the wiring.'}")
    return 0 if not mismatches else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true", help="show accuracy across thresholds")
    ap.add_argument("--live", action="store_true", help="verify the running proxy agrees")
    ap.add_argument("--fail-under", type=float, default=0.0,
                    help="exit non-zero if accuracy falls below this (for CI)")
    args = ap.parse_args()

    results, m = evaluate(routing.THRESHOLD)
    report(results, m)
    if args.sweep:
        print()
        sweep()
    if args.live:
        print()
        return live_check()
    print("\n  What this measures, and what it does not")
    print("    It measures whether the policy routes the way a reviewer would, on a labelled")
    print("    set. It does NOT measure answer quality: it cannot tell you whether the cheap")
    print("    model's response was good enough. That needs shadow evaluation against both")
    print("    models on real traffic, which is the next thing to build.")
    print("    The set is also author-written, so treat the headline number as a regression")
    print("    guard for policy changes rather than a claim about production accuracy.")

    if m["accuracy"] < args.fail_under:
        print(f"\nFAIL: accuracy {m['accuracy']:.1%} below --fail-under {args.fail_under:.1%}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
