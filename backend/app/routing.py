"""Routing policy: decide cheap vs frontier from the request itself.

Design choice: a transparent weighted-signal score, not an LLM judge.

An LLM router would add a frontier-priced call to every request (destroying the
savings we claim), add latency to every turn, and produce a reason no engineering
manager can audit. "The classifier said 0.72" fails the bar. "Contains a Python
traceback and mentions production" does not.

Every decision returns a human-readable reason plus the exact signals that fired,
so the console can show prose and a reviewer can see the receipts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .models import CHEAP, FRONTIER, POLICY_VERSION, ModelSpec

Mode = Literal["routed", "pinned", "routed_with_override"]

# Score at or above this routes to frontier.
THRESHOLD = 4.0

# A signal this strong suppresses the routine-work dampeners entirely.
STRONG_SIGNAL = 3.0


@dataclass(frozen=True)
class Signal:
    name: str
    weight: float
    detail: str


@dataclass
class RouteDecision:
    model: ModelSpec
    mode: Mode
    reason: str
    score: float
    signals: list[Signal] = field(default_factory=list)
    # Stable machine-readable label for the driving signal, so the console can
    # answer "how many requests routed frontier for security?" with a GROUP BY
    # instead of parsing prose.
    reason_code: str = "unclassified"
    policy_version: str = POLICY_VERSION

    @property
    def category(self) -> str:
        return self.model.category

    def signals_json(self) -> list[dict[str, Any]]:
        return [
            {"name": s.name, "weight": s.weight, "detail": s.detail} for s in self.signals
        ]


# --- escalation signals: evidence the turn needs real reasoning ---------------

_TRACEBACK = re.compile(
    r"(traceback \(most recent call last\)|^\s*at [\w.$]+\(|"
    r"\b\w*(?:Error|Exception)\b\s*:|panic:|segmentation fault|stack trace)",
    re.I | re.M,
)
# Note the \w* suffixes: "intermittently", "outages" and "incidents" are the
# forms people actually write, and a bare \b refuses to match them.
_PRODUCTION = re.compile(
    r"\b(production|prod\b|outage\w*|incident\w*|on-call|customers? (?:are|is) |"
    r"intermittent\w*|flaky|works locally|only happens|p99|sev\d)",
    re.I,
)
_CONCURRENCY = re.compile(
    r"\b(race condition|deadlock|livelock|mutex|semaphore|thread[- ]safe|"
    r"concurren\w+|goroutine|data race|lock contention)\b",
    re.I,
)
_SECURITY = re.compile(
    r"\b(security review|vulnerab\w+|exploit|injection|sql ?injection|xss|csrf|"
    r"ssrf|auth[nz]?\b|authentication|authorization|credential|secret|token leak|"
    r"sanitiz\w+|threat model)\b",
    re.I,
)
_ARCHITECTURE = re.compile(
    r"\b(architect\w+|trade[- ]?offs?|design doc|system design|scalab\w+|"
    r"migration plan|schema change|should we use|pros and cons|rearchitect)\b",
    re.I,
)
_DEEP_DEBUG = re.compile(
    r"\b(why\b.{0,40}?\b(?:fail|break|crash|error|hang|leak|slow|wrong)\w*|"
    r"why (?:is|does|do|would|are|did|isn't|won't|can't)|"
    r"root cause|investigat\w+|debug\w*|subtle|reproduc\w+|regression\w*)",
    re.I | re.S,
)

# --- de-escalation signals: evidence the turn is routine ----------------------

_ROUTINE_EDIT = re.compile(
    r"\b(rename|renaming|reformat|format this|add (?:a )?(?:doc ?string|comment|type hint)|"
    r"fix (?:the )?typo|convert to|extract (?:this )?into a (?:function|variable))\b",
    re.I,
)
_SUMMARIZE = re.compile(
    r"\b(summari[sz]e|explain (?:this|the following|what)|what does this (?:function|code|method) do|tl;?dr)\b",
    re.I,
)
_SIMPLE_TEST = re.compile(
    r"\b((?:write|add|generate) (?:a |some )?(?:simple |basic |quick )?unit tests?|"
    r"add test coverage for this function)\b",
    re.I,
)


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return _content_to_text(m.get("content"))
    return _content_to_text(messages[-1].get("content")) if messages else ""


def _content_to_text(content: Any) -> str:
    """OpenAI content is either a string or a list of typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _all_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(_content_to_text(m.get("content")) for m in messages)


def collect_signals(messages: list[dict[str, Any]]) -> list[Signal]:
    last = _last_user_text(messages)
    everything = _all_text(messages)
    signals: list[Signal] = []

    def fire(pattern: re.Pattern[str], text: str, name: str, weight: float, detail: str):
        m = pattern.search(text)
        if m:
            snippet = m.group(0).strip()[:60]
            signals.append(Signal(name, weight, f"{detail} ({snippet!r})"))

    fire(_TRACEBACK, everything, "traceback", 3.5, "stack trace or exception in the request")
    fire(_PRODUCTION, everything, "production_context", 3.0, "references production or live-incident context")
    fire(_CONCURRENCY, everything, "concurrency", 4.0, "concurrency or synchronisation topic")
    fire(_SECURITY, everything, "security", 4.0, "security-sensitive review")
    fire(_ARCHITECTURE, everything, "architecture", 4.0, "architecture or design-tradeoff question")
    fire(_DEEP_DEBUG, last, "deep_debug", 1.5, "root-cause investigation language")

    # Breadth of context: many files or many code blocks means cross-cutting work.
    fences = everything.count("```")
    if fences >= 6:
        signals.append(Signal("multi_file", 2.0, f"{fences // 2} code blocks supplied"))
    paths = set(re.findall(r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|sql|yaml|yml)\b", everything))
    if len(paths) >= 3:
        signals.append(Signal("multi_file_paths", 2.0, f"{len(paths)} distinct file paths referenced"))

    # A rough pre-call size estimate. It is deliberately described in characters
    # rather than tokens: the real token count comes back from upstream after the
    # call and always exceeds this one (chat template, role and special tokens),
    # so quoting a token figure here would contradict the billed number the
    # console shows beside it.
    approx_tokens = len(everything) // 4
    if approx_tokens >= 1500:
        signals.append(Signal("long_context", 2.0, f"large prompt ({len(everything)} characters)"))
    elif approx_tokens >= 600:
        signals.append(Signal("medium_context", 1.0, f"moderate prompt ({len(everything)} characters)"))

    turns = sum(1 for m in messages if m.get("role") == "user")
    if turns >= 6:
        signals.append(Signal("long_conversation", 1.0, f"{turns} user turns"))

    # --- routine work pulls the score down ---
    # Only when nothing serious fired. A two-line security question is still a
    # security question: brevity is evidence of simplicity only in the absence
    # of contradicting evidence.
    if any(s.weight >= STRONG_SIGNAL for s in signals):
        return signals

    fire(_ROUTINE_EDIT, last, "routine_edit", -2.0, "routine mechanical edit")
    fire(_SUMMARIZE, last, "summarize", -1.5, "summarisation or explanation request")
    fire(_SIMPLE_TEST, last, "simple_test", -1.5, "straightforward test generation")
    if approx_tokens < 120 and turns <= 1:
        signals.append(Signal("short_single_turn", -1.0, "brief single-turn prompt"))

    return signals


def _build_reason(chose_frontier: bool, score: float, signals: list[Signal]) -> str:
    if chose_frontier:
        drivers = sorted([s for s in signals if s.weight > 0], key=lambda s: -s.weight)[:2]
        if drivers:
            body = " and ".join(d.detail.split(" (")[0] for d in drivers)
            return (
                f"Frontier: {body}; deeper reasoning justified. "
                f"(score {score:.1f} >= {THRESHOLD:.1f})"
            )
        return f"Frontier: aggregate complexity above threshold. (score {score:.1f} >= {THRESHOLD:.1f})"

    dampeners = sorted([s for s in signals if s.weight < 0], key=lambda s: s.weight)[:2]
    if dampeners:
        body = " and ".join(d.detail.split(" (")[0] for d in dampeners)
        return (
            f"Cheap: {body}; no error, security, or design signals. "
            f"(score {score:.1f} < {THRESHOLD:.1f})"
        )
    return (
        f"Cheap: no complexity signals detected in this request. "
        f"(score {score:.1f} < {THRESHOLD:.1f})"
    )


def decide(
    messages: list[dict[str, Any]], route_hint: str | None = None
) -> RouteDecision:
    """Choose a lane for a virtual-model request."""
    signals = collect_signals(messages)
    score = sum(s.weight for s in signals)

    if route_hint in ("cheap", "frontier"):
        spec = CHEAP if route_hint == "cheap" else FRONTIER
        return RouteDecision(
            model=spec,
            mode="routed_with_override",
            reason=(
                f"{spec.category.title()}: forced by X-Route-Hint header; "
                f"policy would have scored {score:.1f} vs threshold {THRESHOLD:.1f}."
            ),
            score=score,
            signals=signals,
            reason_code="route_hint_override",
        )

    chose_frontier = score >= THRESHOLD
    return RouteDecision(
        model=FRONTIER if chose_frontier else CHEAP,
        mode="routed",
        reason=_build_reason(chose_frontier, score, signals),
        score=score,
        signals=signals,
        reason_code=_reason_code(chose_frontier, signals),
    )


def _reason_code(chose_frontier: bool, signals: list[Signal]) -> str:
    """The single signal that best explains this decision, as a stable label."""
    relevant = [s for s in signals if (s.weight > 0) == chose_frontier and s.weight != 0]
    if not relevant:
        if chose_frontier:
            return "aggregate_complexity"
        # Escalation signals fired but did not clear the threshold, which is a
        # different story from "nothing matched", and the label should say so.
        return "below_threshold" if any(s.weight > 0 for s in signals) else "no_signals"
    dominant = max(relevant, key=lambda s: abs(s.weight))
    return dominant.name


def messages_to_text(messages: list[dict[str, Any]]) -> str:
    """Public helper for callers that need a flat text view of a conversation."""
    return _all_text(messages)
