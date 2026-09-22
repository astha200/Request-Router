# Request-Router

**An OpenAI-compatible proxy that routes coding-agent traffic between a cheap and a frontier
Fireworks model, and a console that makes every one of those decisions auditable.**

Point any OpenAI SDK or coding harness at `http://localhost:8000/v1`, keep working, and open
the console to see exactly where the money went and why.

---

## The product bet

Engineering organizations are forced into a global choice they shouldn't have to make: pay
frontier prices for every coding-agent turn, or accept cheap-model quality everywhere. Neither
is right, because the traffic isn't uniform. Renaming a symbol, summarizing a file, and writing
a basic test are routine. Debugging a production race condition is not.

**The bet:** that split can be made automatically, per request, and the decision can be made
*legible* enough that an engineering manager will actually trust it in a spend review.

**Who it's for.** Two users who want opposite things, which is the design problem.

**The developer** on a coding agent wants their harness to keep working. Success for them is that
nothing changes except the bill: a `base_url` swap, streaming intact, pins honored exactly, and
the upstream response passed back untouched.

**The engineering leader** accountable for model spend has to answer "are we saving money without
tanking quality?" in a budget review, from the console alone. Every request carries a
plain-English reason, and the console shows its own arithmetic including what it excludes.

The router resolves the tension by putting the decision in response headers, invisible unless you
look, and the reasoning in the console, unmissable when you do. It also has to be overrideable:
a routing layer engineers can't override is one they route around. Pinning is a first-class
feature, and the override rate turns out to be one of the most useful quality signals available
(see [What I'd measure](#what-id-measure-in-production)).

One caveat belongs up front. **Cost savings alone do not prove this works.** A router that sends
everything to the cheap model shows spectacular savings and is a bad product. The console gives
routing distribution and route reasons the same prominence as the dollar figure, so a cheap
number can't hide a quality regression. Measuring answer quality is the first thing I'd build
next.

---

## Requirements coverage

Mapped to the assignment brief, with where each is implemented.

**1. Proxy API**

| Requirement | Where |
|---|---|
| `POST /v1/chat/completions` | [main.py](backend/app/main.py) |
| `GET /health` | [main.py](backend/app/main.py) — status, DB reachability, key presence |
| Virtual model id gets routed | `fireworks-router/auto` -> [routing.py](backend/app/routing.py) |
| Concrete model ids are pinned, served exactly | Pin resolved before policy; also beats `X-Route-Hint` |
| Persist route decision, reason, model used | `decision_mode`, `route_reason`, `selected_model` |
| Persist input/output tokens (real or estimated) | `input_tokens`, `output_tokens`, `tokens_estimated` |
| Persist cost, latency | `cost_usd`, `frontier_baseline_usd`, `latency_ms` |
| At least one attribution id | `attribution_id` from `X-User-ID` / `user` / key fingerprint |
| One virtual + one cheap + one frontier id | See [Product contract](#product-contract) |
| Rates per 1M documented | Same table, verified against the Fireworks pricing docs |
| Upstream calls Fireworks OpenAI-compatible API | [fireworks.py](backend/app/fireworks.py) |
| Developer can point an SDK/harness at `base_url` | [Using it](#using-it) — env vars, base_url, model name |

**2. Routing policy**

| Requirement | Where |
|---|---|
| Real policy choosing cheap vs frontier | Weighted signal score, threshold 4.0 |
| Route reason legible to a person, credible to a customer | Prose reason + score + signal JSON on every row |
| Pins must still win | Never rerouted, never reshaped; tested adversarially |

**3. Cost and routing console**

| Requirement | Where |
|---|---|
| Total spend | Stat card |
| Estimated savings vs all-frontier baseline | Stat card + inline methodology note |
| Route mix (% cheap vs frontier) | Stat card with proportional bar |
| Recent requests with decision, reason, cost, latency | Table; reason gets its own full-width row |
| Filter or group by attribution | Dropdown filter + per-user rollup with spend and savings |
| Populated from real proxy traffic | `scripts/demo_traffic.py` sends real requests upstream |
| Console reads the same store the proxy writes | Both use `router.db` via [store.py](backend/app/store.py) |

**4. Write-up and demo**

| Requirement | Status |
|---|---|
| README: run with a key, point a harness, models and rates, how routing works, product bet | This document |
| Demo video | Recorded separately and attached to the submission |

---

## Architecture

```
coding harness / OpenAI SDK
        │  base_url = http://localhost:8000/v1
        │  X-User-ID: astha          (attribution)
        │  X-Route-Hint: frontier    (optional override)
        ▼
┌──────────────────────────────────────────────────┐
│  FastAPI proxy                                   │
│                                                  │
│   routing.py    messages ──► RouteDecision       │  pure, no I/O
│   pricing.py    usage ─────► cost, baseline      │  pure, no I/O
│   models.py     model IDs + prices               │  single source of truth
│   fireworks.py  httpx (streaming + not)          │  only file touching network
│   store.py      SQLite                           │  only file touching the DB
│   admin.py      /admin/* read APIs               │
└──────────────────────────────────────────────────┘
        │                              │
        ▼                              ▼
   Fireworks API                   router.db (SQLite, WAL)
                                        │
                                        ▼
                              React + TS console (Vite)
```

Routing and pricing are pure functions with no dependencies, so most of the test suite runs
without mocking anything. `store.py` and `fireworks.py` are the only modules that perform I/O,
which keeps the failure modes in two well-defined places.

---

## Quickstart

**Requirements:** Python 3.10+, Node 18+, a Fireworks API key.

### 1. Fireworks API key

Get one at [app.fireworks.ai](https://app.fireworks.ai/settings/users/api-keys), then:

```bash
cp .env.example .env
# edit .env and set FIREWORKS_API_KEY=fw_...
```

`.env` is gitignored. The key is never logged, never returned in an error body, and is scrubbed
from any upstream error text before it reaches a client (`config.redact`).

### 2. Backend

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && uvicorn app.main:app --reload --port 8000
```

Confirm the models are live and the configured prices are current — **from the repo root**,
in a second terminal (the venv must be active):

```bash
python scripts/verify_models.py
```

This checks both model IDs against the Fireworks catalog, sends a real completion to each, and
prints the configured rates next to the docs URL to diff them against. **Run it before any
demo** — the Fireworks serverless catalog rotates, and a retired model returns 404.

### 3. Frontend

```bash
cd frontend && npm install && npm run dev
```

Console at **http://localhost:5173**. It proxies `/admin` and `/health` to port 8000, so there
is no CORS configuration to debug.

### 4. Generate traffic

From the repo root, with the backend running:

```bash
python scripts/demo_traffic.py
```

13 real requests through the real proxy to the real Fireworks API: six routine, five complex,
two pinned, across three attribution IDs. Each one asserts its expected lane and prints a `!` on
any mismatch, so the script doubles as an evaluation fixture for policy changes.

To prove SSE streaming with visibly incremental output, which is what decides whether a real
coding harness can use this at all:

```bash
python scripts/demo_stream.py
```

It prints tokens as they arrive, then reports time-to-first-token and the real token counts the
router captured for billing. `--long` asks for a longer answer so the streaming is unmistakable,
and `--raw` prints the server-sent events themselves:

```
   688ms  delta: {"role": "assistant"}
   688ms  delta: {"reasoning_content": "Write"}
   852ms  delta: {"content": "St"}
   852ms  delta: {"content": "ale"}
  1833ms  usage: {"prompt_tokens": 28, "completion_tokens": 96, ...}
  1833ms  data: [DONE]
```

That view shows the whole streaming story in one place: frames arriving separately over an open
connection, the cheap model's reasoning tokens preceding its answer tokens, and the final usage
frame that lets a streamed request be billed on real counts instead of an estimate.

---

## Product contract

| Role | Display name | Model ID | Input /1M | Output /1M |
|---|---|---|---|---|
| **Virtual (routed)** | — | `fireworks-router/auto` | — | — |
| **Cheap** | GLM 5.3 Flash | `accounts/fireworks/models/glm-5p3-flash` | **$0.15** | **$0.50** |
| **Frontier** | Kimi K3 | `accounts/fireworks/models/kimi-k3` | **$3.00** | **$15.00** |

**20× on input, 30× on output.** Rates verified 2026-09-07 against
[docs.fireworks.ai/serverless/pricing](https://docs.fireworks.ai/serverless/pricing) (Standard
serverless tier). Kimi K3 is the global variant, *not* the US-only variant that carries the 50%
premium introduced 2026-09-01.

Everything about a model lives in one place: [`backend/app/models.py`](backend/app/models.py).
Swapping either model is a one-line change, and pricing flows from that dict into the database,
the API, and the console. There is no second copy of a price anywhere.

### A note on the cheap model: reasoning effort

GLM 5.3 Flash is a **thinking-only** model. Left alone it spends most of its output budget on
reasoning tokens, billed at the output rate, before writing a single answer token. In testing,
"rename this variable" cost **68 output tokens (57 of them reasoning)**. With
`reasoning_effort: "low"` the same answer cost **6 tokens**.

So the router sets `reasoning_effort: "low"` on requests it routes to the cheap lane, with two
guardrails:

- **Never on a pinned request.** A pin means *serve exactly what I asked for*; reshaping that
  request would violate the guarantee.
- **Never overriding the client.** If the caller set `reasoning_effort` themselves, theirs wins.

This is a genuine product decision, not a micro-optimization: the cheap lane exists for routine
work, and routine work shouldn't be billed for deliberation.

---

## Routing policy

When a client requests the virtual model, `routing.py` scores the conversation against weighted,
human-readable signals. Score **≥ 4.0** routes to frontier.

**Escalating signals**

| Signal | Weight | Fires on |
|---|---|---|
| `security` | 4.0 | security review, injection, XSS/CSRF, auth, secrets, threat model |
| `architecture` | 4.0 | tradeoffs, system design, scalability, migration plans |
| `concurrency` | 4.0 | race conditions, deadlocks, mutexes, thread safety |
| `traceback` | 3.5 | stack traces, exception classes, panics |
| `production_context` | 3.0 | production, outage, incident, intermittent, "works locally" |
| `multi_file` / `multi_file_paths` | 2.0 | ≥3 code blocks or ≥3 distinct file paths |
| `long_context` | 2.0 | ~1500+ tokens of context |
| `deep_debug` | 1.5 | "why is this failing", root cause, investigate |

**Dampening signals** — `routine_edit` (−2.0), `summarize` (−1.5), `simple_test` (−1.5),
`short_single_turn` (−1.0).

**Dampeners are suppressed whenever a signal ≥3.0 fires.** Brevity is evidence of simplicity
only when nothing contradicts it: *"Security review: is this vulnerable to XSS?"* is terse and
single-turn, and still belongs on the frontier model. A test pins that case.

### Why absence of signals routes cheap

A reasonable alternative is to default ambiguous work to the frontier model "to protect
quality." The argument behind it is sound, and the error costs are asymmetric: a wrong
cheap route costs quality, a retry, and developer trust; a wrong frontier route costs only money.

I still think defaulting to frontier is wrong here, for two reasons.

**It inverts the product.** This exists because most coding-agent traffic is routine. If
everything a policy can't confidently classify goes frontier, and most real phrasing is
unclassifiable to a pattern matcher, you have built a passthrough proxy with a logging sidecar.
The savings number then measures how many prompts happened to match a pattern, not how much
routine work exists.

**It's abstention, not conservatism.** The asymmetry argument justifies a *sensitive escalation
threshold*, with more escalation signals weighted to fire on their own, not making the cheap lane
opt-in. Those are different mechanisms with different failure modes: one you can tune with data,
the other silently caps your ceiling.

So absence of complexity signals routes cheap, hard topics escalate on their own evidence, and
the risk is managed by making the decision visible (`X-Router-Reason` on every response), the
override trivial (`X-Route-Hint`), and the override *rate* the metric that says whether the
threshold is wrong.

I'd revisit this as soon as shadow-eval data showed where the policy's ceiling actually sits.

### Why not an LLM router

A model-based router would add a frontier-priced call to **every** request, directly cannibalizing
the savings being claimed, plus latency on every turn. The disqualifying problem is
explainability: *"the classifier scored 0.72"* is not something an engineering manager can audit
in a spend review. *"Contains a Python traceback and mentions production"* is. The reason string
is the product surface here, and a heuristic router produces a defensible one for free.

The tradeoff: a keyword-and-pattern router will misclassify novel phrasings that a model would
catch. That is a real ceiling, and the mitigations are the override header and the
signal-level logging that makes misroutes diagnosable.

### Route reasons

Every request gets one, generated from the signals that actually drove the decision:

```
Frontier: stack trace or exception in the request and references production or
          live-incident context; deeper reasoning justified. (score 8.0 >= 4.0)

Cheap:    routine mechanical edit and ~14 tokens, single turn; no error, security,
          or design signals. (score -3.0 < 4.0)

Pinned:   client explicitly requested the frontier model (Kimi K3); routing policy
          not consulted.
```

The full signal list (name, weight, and matched text) is persisted as JSON in
`route_signals`, so a misroute can be diagnosed rather than guessed at.

Every row also carries two fields that make the policy analysable, not just readable:

- **`reason_code`** — a stable label for the dominant signal (`security`, `routine_edit`,
  `below_threshold`, `explicit_pin`). The prose reason is for humans; this is what answers
  *"how many requests routed frontier for security this month?"* with a `GROUP BY`.
- **`policy_version`** — bumped whenever weights or the threshold change, so tuning a policy
  doesn't silently contaminate historical averages. You can compare before and after.

Both are also returned as `X-Router-Reason-Code` and `X-Router-Policy-Version` headers.

---

## Pinning behavior

**A pin is an instruction, not a suggestion.**

| Client sends | Result |
|---|---|
| `fireworks-router/auto` | Routing policy decides |
| `accounts/fireworks/models/glm-5p3-flash` | **Always** cheap — policy never consulted |
| `accounts/fireworks/models/kimi-k3` | **Always** frontier — policy never consulted |
| `kimi-k3` (bare slug) | Accepted as a pin; Fireworks expands bare slugs and developers type them |
| anything else | `400` listing the three valid IDs |

Pinned requests are never rerouted, never reshaped, and **never counted as savings**. A pin outranks the `X-Route-Hint` override header too; there is a test for exactly
that.

---

## Attribution

Two mechanisms, in priority order.

**1. OpenAI's standard `user` field** — the typed, native way a client identifies an end user:

```python
client.chat.completions.create(model="fireworks-router/auto", user="team/payments", messages=[...])
```

**2. A header**, for harnesses whose request body you can't modify:

```
X-User-ID: astha
X-Attribution-ID: team-payments
X-Session-ID: sess_abc123
```

**Why both:** `user` is the OpenAI standard and should win when a client sets it. But a coding
harness you can't modify can usually still set a header, so headers are the fallback, then a
truncated API-key fingerprint (`key:sk-abcd12`), then `anonymous`.

**Prompts and completions are never persisted.** The store holds routing and operational
metadata only: decision, reason, matched signal names, tokens, cost, latency. Customer code
should not silently become analytics data.

The console filters and groups by this axis, and recomputes spend, savings, and route mix for
whichever slice you select.

---

## Using it

### OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(api_key="local", base_url="http://localhost:8000/v1")

resp = client.chat.completions.create(
    model="fireworks-router/auto",
    messages=[{"role": "user", "content": "Why does this deadlock under load?"}],
    extra_headers={"X-User-ID": "astha"},
)
print(resp.choices[0].message.content)
print(resp.fireworks_router)   # decision, category, selected_model, reason
```

Streaming works identically; pass `stream=True`. Upstream SSE bytes are passed through
untouched; the router only *peeks* at the final frame to capture real token counts.

### Coding harness

Any harness that accepts an OpenAI-compatible base URL:

```bash
export OPENAI_BASE_URL="http://localhost:8000/v1"
export OPENAI_API_KEY="local"       # the proxy holds the real Fireworks key
export OPENAI_MODEL="fireworks-router/auto"
```

`GET /v1/models` advertises all three IDs, which several harnesses require at startup.

### curl — see the decision in the headers

```bash
curl -i http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-User-ID: astha' \
  -d '{"model":"fireworks-router/auto",
       "messages":[{"role":"user","content":"Rename usr to user"}]}'
```

Every response carries `X-Router-Decision`, `X-Router-Category`, `X-Router-Model`,
`X-Router-Reason`, and `X-Request-ID` — so a developer can see routing without opening the
console.

---

## Cost and savings methodology

Per request:

```
cost = (input_tokens / 1M × input_rate) + (output_tokens / 1M × output_rate)
```

Token counts come from the upstream `usage` block. They are real, not estimated, including on streamed
requests via `stream_options.include_usage`. If a stream dies before the usage frame, the row is
billed on a character estimate and **flagged `Est` in the console** instead of quietly counted
as exact.

Savings:

```
baseline = Σ frontier_cost(same tokens)   over ROUTED requests only
savings  = baseline − Σ actual_cost       over ROUTED requests only
```

**Two honesty constraints, both visible in the console:**

1. **Pinned requests are excluded from savings.** The developer chose that model, not the
   router, so the router doesn't claim credit. Pinned spend is reported separately and still
   counts toward total spend — `routed_spend + pinned_spend == total_spend`, asserted in a test.
2. **The baseline assumes identical token counts.** A frontier model would in reality emit a
   different number of output tokens for the same prompt, so the baseline is a well-defined
   *estimate*, not a measurement. The console states this inline, not just here.

A representative run of `demo_traffic.py`: **~$0.04 spent, ~$0.03 saved (roughly 45% below the
all-frontier baseline), ~54% cheap / ~46% frontier.** That figure is not 95%, and shouldn't be: the
frontier lane is doing real work on the requests that earned it. A router
showing 95% savings on this traffic would be one that stopped escalating.

---

## API reference

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/chat/completions` | Proxy. Streaming + non-streaming |
| `GET` | `/v1/models` | Advertises the three model IDs |
| `GET` | `/health` | Status, DB reachability, key presence (never the key) |
| `GET` | `/admin/summary?attribution_id=` | Spend, savings, route mix |
| `GET` | `/admin/requests?attribution_id=&limit=` | Recent requests |
| `GET` | `/admin/attributions` | Per-user rollup |

**Errors** — OpenAI-shaped envelopes throughout: `400` malformed/unknown model (lists valid
IDs), `502` upstream failure, `504` timeout, `503` missing API key. Upstream error text is
redacted before it's returned. Failed requests are still persisted with a `status`, so the
console shows failures instead of silently dropping them.

**Client disconnects are handled explicitly.** If a harness cancels a streaming turn, the
proxy closes the upstream Fireworks stream *explicitly* instead of waiting for garbage
collection. Otherwise the connection stays open and you keep paying for tokens nobody will
read. The partial request is recorded with status `client_disconnected` rather than vanishing.

**A database failure never fails a developer's request.** `store.record` swallows and logs.
Analytics is not on the critical path.

---

## Packaging a submission

```bash
bash scripts/package_submission.sh
```

Writes `submission/fireworks-router-submission.zip` (~68KB). It excludes credentials, the
virtualenv, `node_modules`, the local database, build output and git history, then **verifies
the archive contains no key-shaped strings before writing it**, and aborts if it finds any.
Shipping a secret is the one packaging mistake that can't be walked back, so the check is a
hard gate, not a reminder.

---

## Evaluating the routing policy

```bash
python eval/run_eval.py            # score the policy
python eval/run_eval.py --sweep    # accuracy across thresholds
python eval/run_eval.py --live     # verify the running proxy agrees with the policy
```

45 labelled prompts in [`eval/cases.py`](eval/cases.py), scored offline in milliseconds
(routing is a pure function, so this costs nothing to run). Current result:

```
accuracy   84.4%   (38/45)
precision  85.0%   of escalations that were warranted
recall     81.0%   of work needing frontier that got it
```

**Why the number isn't higher, and why that's the honest one.** 35 of the cases are
straightforward and the policy gets all 35 right. The other 10 are written specifically to
attack pattern matching, and that's where every failure is:

| Failure mode | Score | What it looks like |
|---|---|---|
| Casual phrasing of a hard problem | **0/3** | *"Users on slow connections sometimes end up charged twice"* — a concurrency bug with none of the vocabulary |
| Keyword bleed into routine work | **1/4** | *"Rename `tok` to `token` in our auth middleware"* — a rename that over-escalates on "auth" |
| Genuinely ambiguous | 2/3 | *"Make this function faster"* |

Those numbers are the measured ceiling of the approach, and they're the reason the roadmap
leads with quality measurement instead of more patterns. **I did not tune the
policy against these ten cases** — over-fitting to ten prompts I wrote myself would make the
score go up and the product no better.

Note also the direction of the errors. Over-escalation costs money only; under-escalation costs
quality and probably a retry. The three keyword-bleed failures are the safe direction; the three
casual-phrasing failures are not, and they're the ones worth fixing with real data.

### Why the threshold is 4.0

`--sweep` shows accuracy is flat from 1.0 through 4.0 and falls off a cliff at 4.5, where recall
collapses to 38%. **4.0 is the highest threshold that still preserves recall** — the cheapest
point on the stable plateau, not an arbitrary pick. Four tests pin that property, so if
signal weights shift and 4.0 stops being the plateau edge, CI says so.

**What this measures and what it does not:** it measures whether the policy routes the way a
reviewer would. It does not measure answer quality: it cannot tell you whether the cheap model's
answer was good enough. That needs shadow evaluation against both models on real traffic. The set
is also author-written, so it works as a regression guard for policy changes, not as a claim
about production accuracy.

---

## Tests

```bash
cd backend && pytest -q     # 76 tests, ~0.7s
```

Covering, among others, every behavior the brief called for:

| Behavior | Test |
|---|---|
| Simple request routes cheap | `test_simple_requests_route_cheap` (4 cases) |
| Complex/debugging routes frontier | `test_complex_requests_route_frontier` (4 cases) |
| Cheap pin stays cheap | `test_cheap_pin_stays_cheap_even_for_a_complex_prompt` |
| Frontier pin stays frontier | `test_frontier_pin_stays_frontier_even_for_a_trivial_prompt` |
| Pin beats the override header | `test_route_hint_cannot_override_a_pin` |
| Cost calculation | `test_cost_worked_example`, `test_cost_uses_published_per_million_rates` |
| Savings calculation | `test_savings_is_baseline_minus_actual_over_routed_requests` |
| Pinned excluded from savings | `test_pinned_requests_are_excluded_from_savings` |
| Unknown model rejected | `test_unknown_model_is_rejected_with_valid_options` |
| Route reason always present | `test_every_decision_has_a_human_readable_reason` |
| Key never leaks on error | `test_upstream_failure_returns_502_without_leaking_the_key` |
| Streamed bytes pass through untouched | `test_streamed_bytes_pass_through_untouched` |
| Streaming bills on real tokens | `test_streaming_captures_real_usage_not_an_estimate` |
| Missing usage frame is flagged estimated | `test_streaming_without_a_usage_frame_is_flagged_estimated` |
| Client hangup closes upstream and is recorded | `test_client_disconnect_mid_stream_is_recorded` |
| Natural debugging phrasings escalate | `test_natural_debugging_phrasings_reach_frontier` |

---

## What I'd measure in production

Cost metrics are the easy half. The hard half is proving the router isn't quietly degrading
quality, and I'd instrument for that from day one.

**Cost & efficiency**
- Cost per request, per session, and per attribution ID (p50/p95, not just mean)
- Savings vs all-frontier baseline, always shown next to route mix so one can't hide the other
- Cheap/frontier route mix, tracked as a *trend* — a mix drifting toward cheap is the early
  warning that a policy change went too far

**Quality — the metrics that actually decide whether this ships**
- **Manual override / pin rate.** The strongest early signal available. Developers pinning
  frontier on turns the router sent cheap is a direct, unambiguous vote that the policy is
  wrong, and it needs no labeling infrastructure.
- **Retry and escalation rate.** How often does a cheap response get immediately re-asked, or
  re-run against frontier? A cheap turn that gets retried on frontier didn't save money; it
  cost 1.2× and burned developer patience.
- **Acceptance rate by lane.** For coding agents: were the proposed edits kept or reverted?
  Diff acceptance split by cheap vs frontier is the closest honest proxy for quality.
- **Offline eval on shadow traffic.** Sample real routed prompts, run both models, score with a
  rubric or an LLM judge *offline* where its cost and latency don't touch the request path.
  This is what turns the threshold from a guess into a tuned parameter.

**Reliability**
- Router overhead (proxy latency minus upstream latency); the router must be invisible
- Upstream error and timeout rate by model
- Rate of requests billed on estimated tokens instead of real ones

The framing I'd hold a team to: **savings is the headline, override rate and retry rate are the
truth.** If savings goes up while override rate goes up, the router got worse, not better.

---

## What I'd build next

1. **Quality measurement.** The eval harness scores *routing*; the missing half is scoring
   *answers*. Shadow-run routed prompts against both models offline and report per-lane win
   rates, so the cheap lane's quality is measured instead of assumed. The harness, the labelled
   set, and the CI guard already exist — this extends them from "did it route right?" to "was the
   answer good enough?" Unambiguously first.
2. **Escalation on failure.** If a cheap response fails a cheap check — empty diff, syntax
   error, explicit user retry — automatically re-run on frontier and record it as an escalation.
   Turns the worst failure mode into a recoverable one.
3. **Per-team budgets and policy.** Attribution already exists; budgets, alerting, and
   per-team thresholds are the natural next layer for the admin who's actually accountable.
4. **Prompt-prefix caching awareness.** Fireworks prices cached input at a large discount
   ($0.30 vs $3.00 on Kimi K3). Long agent conversations resend enormous prefixes; routing that
   accounts for cache state would change the math materially.
5. **Threshold tuning from data.** 4.0 is a considered default, not a fitted parameter. With
   override and retry data it becomes a fitted parameter.

---

## Known limitations

- **The all-frontier baseline is an estimate.** It assumes the frontier model would emit the same
  number of output tokens for the same prompt. It wouldn't. Directionally sound, not exact, and
  the console says so inline, not only here.
- **Routing quality is measured; answer quality is not.** The eval harness scores whether the
  policy routes the way a reviewer would (84.4%). It cannot tell you whether the cheap model's
  *answer* was good enough — that needs shadow evaluation against both models on real traffic,
  which is the first item on the roadmap above.
- **Pattern-based routing has a measured ceiling.** 84.4% overall, and 0/3 on hard problems
  phrased casually — a concurrency bug described as "sometimes users get charged twice" routes
  cheap, because none of the vocabulary is there. The override header and signal-level logging
  make those cases diagnosable; they don't solve them.
- **`reasoning_effort: "low"` on the cheap lane is a trade.** A large win on routine turns, and
  possibly a small quality cost on borderline ones. Confined to requests the policy already
  judged routine, and never applied to a pinned request.

---

## Notes on this submission

**Time spent:** roughly 6 hours of focused work.

| Phase | Time |
|---|---|
| Model verification, pricing, project skeleton | ~0:30 |
| Fireworks proxy, streaming and non-streaming | ~1:15 |
| Routing policy and route reasons | ~0:40 |
| Persistence, cost and savings accounting | ~0:55 |
| Admin console | ~1:10 |
| Demo traffic, tests, evaluation harness | ~1:00 |
| README and packaging | ~0:30 |

Model verification came first on purpose. A dashboard doing confident arithmetic on retired
model IDs or stale prices is the worst outcome available, and thirty minutes rules it out.

**AI assistance:** AI tools were used selectively during development for brainstorming,
debugging, documentation refinement, and discussing implementation trade-offs. The final
architecture, implementation, testing, and integration were completed and validated by me.

### External packages

Deliberately small. Every dependency is load-bearing.

**Backend** — `fastapi` + `uvicorn` (proxy and admin API), `httpx` (async upstream calls with
streaming), `pydantic` (validation, via FastAPI), `python-dotenv` (key loading), `pytest`
(tests), `openai` (used only by the demo script and tests, to prove real SDK compatibility
instead of a hand-rolled client).

**Frontend** — `react` + `react-dom`, `vite` (dev server and build), `typescript`. Two runtime
dependencies total. No component library, no CSS framework, no charting library: the console is
~250 lines of hand-written CSS, which keeps the repo readable and the bundle at 48KB gzipped.

**No ORM, no state manager, no UI kit.** SQLite is accessed through Python's standard `sqlite3`.
At this scale an ORM would add a dependency and a layer of indirection without removing any work.
