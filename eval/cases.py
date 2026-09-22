"""Labelled routing cases: the evaluation set the policy is scored against.

These are held separately from the unit tests on purpose. Tests answer "did I
break something?" with a pass/fail. This set answers "how good is the policy?"
with a number - which is what makes threshold changes reviewable instead of
argued about.

Each case is (prompt, expected_lane, tag). `tag` groups cases so the report can
show which *kinds* of request the policy handles badly, not just an overall score.
"""

Case = tuple[str, str, str]

CASES: list[Case] = [
    # ---------------- routine work: should route cheap --------------------
    ("Rename the variable `usr` to `user` in this function.", "cheap", "rename"),
    ("Rename the class OrderMgr to OrderManager across this file.", "cheap", "rename"),
    ("Fix the typo in this variable name: recieved_count = 0", "cheap", "rename"),
    ("Add a docstring to this function and return only the code.", "cheap", "docs"),
    ("Add type hints to this helper.", "cheap", "docs"),
    ("Write a short comment explaining what this loop does.", "cheap", "docs"),
    ("Summarize what this function does in one sentence.", "cheap", "summarize"),
    ("Explain what this short helper does.", "cheap", "summarize"),
    ("What does this regex match? ^[a-z]+_[0-9]{2}$", "cheap", "summarize"),
    ("Write a simple unit test for add(a, b).", "cheap", "tests"),
    ("Add a basic pytest case for the happy path of slugify().", "cheap", "tests"),
    ("Write a straightforward unit test for this validator.", "cheap", "tests"),
    ("Convert this for loop to a list comprehension.", "cheap", "transform"),
    ("Format this JSON snippet.", "cheap", "transform"),
    ("Sort these imports alphabetically.", "cheap", "transform"),
    ("Change this function to use f-strings instead of .format().", "cheap", "transform"),
    ("Extract these three lines into a helper function.", "cheap", "transform"),
    ("Turn this dict access into .get() with a default.", "cheap", "transform"),

    # ---------------- work that earns frontier ----------------------------
    ("Do a security review of this Flask endpoint for SQL injection and missing authorization.",
     "frontier", "security"),
    ("Is this login handler vulnerable to CSRF?", "frontier", "security"),
    ("Review this token refresh logic for secret leakage.", "frontier", "security"),
    ("Security review: is this vulnerable to XSS?", "frontier", "security"),
    ("We have a race condition between two goroutines writing the same map. How do I fix it safely?",
     "frontier", "concurrency"),
    ("This async worker deadlocks under load. The mutex is held during read but not write-back.",
     "frontier", "concurrency"),
    ("Why does this thread pool intermittently hang after ~2 hours?", "frontier", "concurrency"),
    ("Our checkout service returns 500 intermittently in production, only at peak. "
     "Traceback (most recent call last):\n  File 'pay.py', line 88\n    ValueError: token expired\n"
     "It never reproduces locally. Walk me through the root cause.", "frontier", "prod_debug"),
    ("Why is this failing intermittently?", "frontier", "prod_debug"),
    ("We had an outage last night. Investigate the root cause from these logs.",
     "frontier", "prod_debug"),
    ("Production traceback ValueError why failing under load?", "frontier", "prod_debug"),
    ("Why does the worker crash in prod but not locally?", "frontier", "prod_debug"),
    ("There were two incidents this week with the same regression.", "frontier", "prod_debug"),
    ("Should we use event sourcing or a CRUD schema for our billing ledger? We need "
     "auditability, 50k writes/day, and point-in-time balance reconstruction.",
     "frontier", "architecture"),
    ("Walk me through the tradeoffs of moving this monolith to services at our scale.",
     "frontier", "architecture"),
    ("What's the migration plan to move from session cookies to short-lived JWTs with "
     "refresh rotation, across auth/session.py, auth/tokens.py and api/middleware.py?",
     "frontier", "architecture"),
    ("Design a schema change that keeps backward compatibility during a rolling deploy.",
     "frontier", "architecture"),
]


def counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for _, lane, _ in CASES:
        out[lane] = out.get(lane, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Deliberately hard cases.
#
# A policy scored only on prompts its author wrote will score 100% and prove
# nothing. These are chosen to attack known weaknesses of pattern matching:
# routine work that mentions a scary word, hard work phrased casually, and
# genuinely ambiguous requests. Failures here are the honest measure of the
# ceiling, and they are expected.
# ---------------------------------------------------------------------------

HARD_CASES: list[Case] = [
    # Routine work contaminated by a high-signal word.
    ("Rename the variable `tok` to `token` in our auth middleware.", "cheap", "hard_keyword_bleed"),
    ("Add a docstring to the function that parses our production config.",
     "cheap", "hard_keyword_bleed"),
    ("Fix the typo in this comment about the security review process.",
     "cheap", "hard_keyword_bleed"),
    ("Summarize what this concurrency utility does.", "cheap", "hard_keyword_bleed"),

    # Hard work phrased casually, with none of the usual vocabulary.
    ("This function returns the wrong total when two users check out at the same moment.",
     "frontier", "hard_casual_phrasing"),
    ("Sometimes the number is off by one cent and I can't work out where it comes from.",
     "frontier", "hard_casual_phrasing"),
    ("Users on slow connections sometimes end up charged twice.",
     "frontier", "hard_casual_phrasing"),

    # Genuinely ambiguous; reasonable engineers would disagree.
    ("Make this function faster.", "cheap", "hard_ambiguous"),
    ("Clean up this module.", "cheap", "hard_ambiguous"),
    ("Add a test for the retry path when the upstream times out and the breaker opens.",
     "frontier", "hard_ambiguous"),
]

ALL_CASES: list[Case] = CASES + HARD_CASES
