# Shift-Change Request Agent — Design

## 1. What this is and why

Employees of client companies message the transport desk to change a cab trip
("can I leave at 8 tonight?", "I'm on leave tomorrow"). A human desk executive
pulls the roster, checks the company's cut-off rule, looks for a cab with a free
seat, escalates to the company's transport manager when a rule is broken, then
applies the change. That does not scale across many companies at evening peak,
rules get applied unevenly, and nobody keeps a clean record.

This service replaces that desk conversation with a chat agent that:

- understands the request in plain language and asks when it is ambiguous,
- reads only the calling employee's roster,
- applies that company's rules **in code**, exactly,
- applies the change, or explains why not and offers the next option,
- escalates to the manager, survives a restart while waiting, and reports the
  decision back into the same conversation,
- always tells the employee, in plain words, what happened and what to do next.

**Guiding principle: the LLM understands and talks; code decides.**

## 2. Goals, non-goals, anti-goals

Goals
- Correct rule enforcement regardless of model output.
- Strict multi-tenant isolation, proven by tests.
- p95 reply < 5 s with the real LLM; ≤ 4 LLM calls per employee message.
- Durable manager-approval path (Postgres checkpoints).
- Rerunnable tests and a JSONL evaluation set with a fake LLM.
- Bonus: exactly one seat / exactly one request under races and double-submit.

Non-goals
- A realistic backend (routing, vendor APIs, real IdP). All are small mocks.
- Arbitrary route changes (pickup address changes, new routes). Out of scope;
  the agent says so.
- Seat reservation while a request waits for approval (see trade-offs).

Anti-goals (fails even if it "works")
- The model ever states a confirmation that code did not produce.
- Identity or company taken from the message body or from model output.
- A stack trace, a raw error, or a silent hang reaching the employee.
- Guessing a time instead of asking.

## 3. Constraints

- Stack: Python 3.12, FastAPI, LangGraph, Postgres, JWT. Frontend: React +
  Vite + shadcn/ui.
- Free LLM tier only (Groq). Tests never call a real LLM.
- One command to start: `docker compose up`. Tests: `docker compose run --rm tests`.
- No API keys committed; `.env.example` only.

## 4. Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| A | Free tool-calling loop (ReAct), capped at 4 calls | Most "agentic"; but 2–4 calls → latency variance, and the model writes the final text, so a false "confirmed" is possible. Hardest to test. | Rejected |
| B | One-shot extraction to JSON, everything else code, template replies | 1 call, fastest, fully deterministic; but "tool selection" degenerates into intent classification and replies feel robotic. | Rejected |
| C | **Planner + executor**: LLM call 1 plans tool calls; code validates, injects identity, executes tools and rules; LLM call 2 phrases the reply from structured facts, guarded, with template fallback | Normally 2 calls (3 with one planner retry, hard cap 4); predictable latency; real tool selection that can be evaluated; facts never come from the model. | **Chosen** |

## 5. Architecture

```
                 ┌──────────── FastAPI ────────────┐
 Browser (SPA) ─▶│ /auth/token (dev JWT issuer)     │
  shadcn UI      │ /chat  (employee JWT)            │
                 │ /manager/* (manager JWT)         │
                 │ /dev/faults (demo only)          │
                 └──────┬──────────────────┬───────┘
                        │                  │
               Chat graph (LangGraph)   Approval graph (LangGraph)
               plan → execute → respond  notify → ⏸ interrupt → apply → notify employee
                        │                  │
                 Tools (tenant-scoped services, fault-injectable)
                        │
                 Postgres: domain tables + audit log + LangGraph checkpoints
```

Single backend container serves the API and the built SPA; one Postgres
container. No Redis: one store is simpler to operate and explain, and Postgres
gives row locks, unique constraints and the LangGraph checkpointer.

### 5.1 Identity and isolation

- JWT (HS256, secret from env) claims: `sub` (employee id), `company_id`,
  `role` (`employee` | `manager`), `exp`.
- `POST /auth/token {employee_id}` is a mock issuer, enabled only when
  `DEV_AUTH=true`. It looks the employee up and signs their real company and
  role. In production this would be the client's IdP.
- Every request body model uses `extra="forbid"`; a body carrying `company_id`
  or `employee_id` is rejected with 422.
- A `TenantContext(company_id, employee_id, role)` is built only from the
  verified token and passed explicitly to every repository function. There is
  no unscoped query function for tenant tables. Every query has
  `WHERE company_id = %(company_id)s`.
- Tool signatures contain only business arguments (date, direction, time).
  Identity is injected by the executor from graph state, which the API sets
  from the token. Unknown arguments (e.g. a model emitting `employee_id`) make
  the plan invalid.
- Cross-tenant lookups return 404, not 403, so existence is not leaked.
- Conversations are rows owned by `(company_id, employee_id)`; a thread id from
  another employee is rejected.
- Postgres row-level security was considered as defence in depth; left as a
  documented future step to keep the code small.

### 5.2 Data model (Postgres, plain SQL via psycopg 3)

- `companies(id, name, timezone, cutoff_minutes_pickup, cutoff_minutes_drop, max_seats_per_cab, allow_escalation)`
- `employees(id, company_id, name, role, route_id)`
- `routes(id, company_id, name)`
- `trips(id, company_id, route_id, direction, trip_date, scheduled_at, vehicle_capacity, vehicle_no)`
  — a cab run. Occupancy = count of active roster rows on it.
- `roster(id, company_id, employee_id, trip_date, direction, trip_id, status)`
  — unique active `(employee_id, trip_date, direction)`.
- `conversations(id, company_id, employee_id, created_at)`
- `messages(id bigserial, conversation_id, company_id, role, text, created_at)` — the
  display log the UI polls; the chat graph state holds the LLM context.
- `shift_change_requests(id, company_id, employee_id, conversation_id, roster_id, change_type, from_trip_id, to_trip_id, status, reason_code, reason_text, dedupe_key, approval_thread_id, created_at, decided_by, decided_at, note)`
  - status: `APPLIED`, `PENDING_APPROVAL`, `APPROVED_APPLIED`, `MANAGER_REJECTED`, `APPROVED_NO_SEAT`, `EXPIRED`, `WITHDRAWN`
  - partial unique index on `(company_id, dedupe_key) WHERE status = 'PENDING_APPROVAL'`
- `audit_log(id, company_id, actor_type, actor_id, action, request_id, payload jsonb, created_at)`

Sample data: two companies, seeded relative to "today" in Asia/Kolkata.

| | Acme Tech | Globex Corp |
|---|---|---|
| Drop cut-off | 120 min | 60 min |
| Pickup cut-off | 240 min | 120 min |
| Max seats per cab (policy) | 4 | 6 |
| Escalation allowed | yes | yes |

Each company: 2 routes, ~6 employees, 1 manager; drop slots every 30 min
17:00–23:30, pickup slots every 30 min 06:00–10:30, today and tomorrow. Some
slots are seeded full to exercise "nearest slot".

### 5.3 Clock

A `Clock` interface. Tests use a fixed clock. Docker demo uses a shifted clock
(`DEMO_START=17:30` → now = today 17:30 IST + real elapsed time since boot) so
the recording works at any wall-clock hour. Real clock otherwise.

### 5.4 Business rules (pure functions, `rules.py`)

Given company policy, the current roster entry, the target trip and `now`:

1. **Ownership**: the roster entry belongs to the caller (enforced by the
   scoped query, re-checked).
2. **Window**: target date is today or tomorrow; target trip is same company,
   same route, same direction, same date.
3. **Cut-off**: `now ≤ current.scheduled_at − cutoff` **and**
   `now ≤ target.scheduled_at − cutoff`. Violation → `CUTOFF_PASSED`
   (escalatable if `allow_escalation`).
4. **Capacity**: `occupancy(target) < min(vehicle_capacity, max_seats_per_cab)`.
   Violation → `NO_SEAT` (not escalatable — a manager cannot create a seat).
5. **Cancellation** (leave/WFH): only rule 3 against the current trip.

Result: `Allowed` or `Violation(code, human_reason, escalatable)`.
On a violation, `nearest_alternatives()` returns up to 2 slots on the same
route/direction/date that pass all rules, ordered by distance from the
requested time.

### 5.5 Booking (`booking.py`) — the only code that writes the roster

One transaction:
1. `SELECT … FROM roster WHERE id=… AND company_id=… FOR UPDATE`
2. Lock involved trip rows `FOR UPDATE` in ascending id order (no deadlocks).
3. Re-evaluate rules against fresh occupancy.
4. Idempotency: if the roster already points at the target trip, return the
   existing `APPLIED` request ("already done") instead of creating a new one.
   For escalation, the partial unique index on `dedupe_key`
   (`hash(employee, roster_id, change_type, to_trip_id)`) makes a duplicate
   insert return the existing pending request.
5. Update roster, insert request, insert audit row, commit.

This gives the bonus guarantees: two employees racing for the last seat —
the trip row lock serialises them, exactly one wins, the other gets
`NO_SEAT` + alternatives. A double-submit — the roster row lock serialises
them; the second sees "already applied" or hits the unique index; exactly one
request exists.

### 5.6 Tools (mock services behind functions)

All take `TenantContext` injected by code plus model-supplied business args
validated by Pydantic (`extra="forbid"`):

| Tool | Args from model | Does |
|---|---|---|
| `get_roster` | `date?` | Caller's trips for today/tomorrow |
| `check_cab_options` | `date, direction, desired_time` | Read-only: is that slot allowed and free, plus nearest alternatives |
| `raise_shift_change_request` | `date, direction, change_type (reschedule\|cancel), desired_time?, escalate=false` | Runs booking. Returns `APPLIED`, `REJECTED(+alternatives, escalatable)`, or (when `escalate=true` and escalatable) `PENDING_APPROVAL` |
| `get_request_status` | `request_id?` | Caller's latest/that request |
| `withdraw_request` | `request_id?` | Withdraws caller's pending request |
| `ask_clarification` | `question, missing[]` | Pseudo-tool: planner asks instead of guessing |

Each service call is wrapped with a timeout (1.5 s) and, for read-only tools,
one retry. A fault-injection layer (`/dev/faults`, tests) can add latency or
errors per tool to demonstrate failure handling.

Escalation needs consent: a cut-off violation is first explained with the next
option and "Do you still want me to send it to your manager?". Only a follow-up
"yes" makes the planner call `raise_shift_change_request(escalate=true)`.

### 5.7 Chat graph (LangGraph, Postgres checkpointer)

Thread id = conversation id. State: `messages` (last N kept for the prompt),
`tenant` (set by API from JWT, never by a node from model output),
`pending_offer` (alternatives last shown), `llm_calls`, `turn_started_at`,
`outcomes` (structured facts for this turn).

Nodes:
1. **plan** (LLM call 1): system prompt with current local date/time, company
   name, the caller's compact roster summary (scoped, loaded by code), tool
   schemas, rules for clarification ("never invent a time; vague → ask").
   Output: native tool calls. Invalid output (unknown tool, bad args, > 3
   calls, extra identity args) → one retry with the validation error (LLM call
   2). Still invalid → template "I didn't quite get that…".
2. **validate** (code): Pydantic per tool; resolve "8" against direction and
   current trip (drop at 19:00 + "8" → 20:00); if a required slot is missing
   or the resolved time is in the past / > 12 h away → convert to a
   clarifying question (code-enforced "ask, don't guess").
3. **execute** (code): run tools in order; stop the sequence at the first
   rejection or failure; record structured outcomes.
4. **respond** (LLM call ≤ 3): phrase a reply from `outcomes` facts only.
   Guard: if the text claims success ("confirmed", "booked", "done",
   "approved"…) without an `APPLIED` outcome, or omits the applied time /
   request id, use the template. If the turn budget left is < 1.5 s, skip the
   LLM and use the template. Key facts (times, cab, request id) are appended
   by code, never left to the model.

**LLM budget**: a per-message `LLMBudget` counter in the client wrapper
raises at the 5th call. Normal: 2. With planner retry: 3. Never more than 4
by construction; asserted in tests over every eval case.

**Offline mode**: when no `GROQ_API_KEY` is set, a small regex-based
`RulePlanner` (times, "cancel/leave/WFH", "status", "yes") stands in for the
planner and replies use templates, so `docker compose up` works for a
reviewer without a key. It is deliberately simple and is not used in the
real-LLM evaluation.

**One turn at a time per conversation**: a second message arriving while the
previous one is still being processed gets 409 "Still working on your last
message" (Postgres advisory try-lock on the conversation id), so a
double-clicked send cannot run two turns on one thread.

**Latency budget** (per message, 4.5 s soft): planner timeout 2.5 s, tools
1.5 s each, responder 1.5 s or skipped. Groq gpt-oss-20b typically answers
in 0.3–0.9 s.

### 5.8 Approval graph (LangGraph, separate thread `approval:{request_id}`)

When a request becomes `PENDING_APPROVAL`, the chat turn starts an approval
thread and replies immediately ("Sent to your manager at 18:05; your 19:00
drop stays booked meanwhile; I'll post the decision here"). The chat stays
usable for status questions, other trips or withdrawal.

Nodes: `record` → `await_decision` (`interrupt()`; checkpointed in Postgres)
→ `apply_decision` (code: approve → booking re-check; full now →
`APPROVED_NO_SEAT` + alternatives; reject → `MANAGER_REJECTED` with note)
→ `notify_employee` (appends a message into the employee's chat thread state
and the conversation's message log).

Resume sources, all via `Command(resume=…)` on that thread:
- manager: `POST /manager/requests/{id}/decision` (company-scoped lookup),
- employee: `withdraw_request`,
- sweeper: a background task every 60 s expires requests whose target trip is
  inside the cut-off or departed → `EXPIRED`, employee told the original trip
  is unchanged and to call the desk if urgent.

The decision is applied idempotently: resuming an already-decided request
returns 409 with the current status. Because both the interrupt and the
request row live in Postgres, a restart between pause and decision is
invisible; the request row maps `request_id → approval_thread_id`.

Why a separate thread instead of interrupting the chat thread: an approval
can take hours; interrupting the chat would block every other message in that
conversation. The pause is still a LangGraph interrupt, and the decision still
lands in the same conversation.

### 5.9 Failure handling (what the employee sees)

| Situation | Reply (template, always available) |
|---|---|
| Tool timeout / error | "I couldn't reach the cab schedule just now, so nothing was changed. Your 19:00 drop is still booked. Please try again in a minute, or call the transport desk if it's urgent." |
| Write outcome uncertain | Re-read by dedupe key and report the true state; never assume success. |
| LLM down / quota / timeout | "I'm having trouble understanding messages right now. Nothing was changed. Please try again shortly or call the desk." |
| Malformed model output twice | "Sorry, I didn't quite get that. Could you tell me the time and whether it's your pickup or drop?" |
| Cab filled while deciding / during approval | "That cab filled up before I could book it. The nearest free one is 20:30. Want that?" |
| Stuck in approval | Status shows since when; expiry message as above. |
| Unexpected exception | Generic plain message + correlation id; stack trace only in server logs. |

A global exception handler guarantees a JSON reply on every path.

### 5.10 API

- `POST /auth/token {employee_id}` → `{token}` (dev only)
- `GET /auth/demo-users` → people to sign in as, grouped by company (dev only)
- `GET /me` → profile; `GET /me/roster`
- `POST /chat {conversation_id?, message}` → `{conversation_id, reply, outcomes[], llm_calls}`
- `GET /conversations/{id}/messages?after=` → for polling approval updates
- `GET /manager/requests?status=` / `POST /manager/requests/{id}/decision {decision, note?}` (role=manager, own company)
- `GET /manager/audit`
- `POST /dev/faults` (dev only)

### 5.11 Frontend (React + Vite + TypeScript + shadcn/ui)

Clean, minimal, three views:
- **Sign in**: pick a demo person (grouped by company) → token.
- **Employee**: chat (Card, ScrollArea, Input, Button), side panel with
  today/tomorrow trips and request status badges; polls messages every 3 s.
- **Manager**: pending requests table with rule broken, requested vs current
  time, Approve / Reject dialog with note; audit tab.
- Small **dev drawer** to toggle tool faults for the demo.

Built in a multi-stage Docker image and served by FastAPI as static files.

## 6. Testing and evaluation

- `pytest` against a real Postgres from compose (`docker compose run --rm tests`).
- Unit: rules (cut-off boundaries per company, capacity, alternatives), time
  resolution, JWT, response guard.
- Integration (FastAPI TestClient + FakeLLM scripted per case): isolation
  (token for company A can't read/touch B's roster, requests, conversations;
  body identity rejected; model-emitted identity args rejected), cut-off
  rejection, clarification and context carryover, escalation → restart (app
  and checkpointer torn down and rebuilt) → manager decision → message in the
  same conversation, tool timeout, LLM failure, malformed output, budget cap,
  last-seat race and double-submit with concurrent tasks.
- **Eval set** `eval/cases.yaml` → runner `python -m eval.run [--real-llm]`
  writes `eval/results.jsonl` with exactly `id, input, expected, actual, pass,
  notes`. Fake mode is deterministic (plumbing + rules). Real mode measures
  actual tool selection and clarification behaviour of the model and records
  latency; its committed results and failures go into `EVALUATION.md`.
- `python -m eval.latency` → p95 over ~40 real-LLM messages.

## 7. Trade-offs (to be expanded in README)

1. Planner/executor over a free agent loop: predictability and a hard call
   cap over flexibility.
2. No seat hold during approval: seats are scarce and approvals take hours;
   holding would block others. Cost: an approved change can find the cab full
   → handled explicitly with alternatives.
3. Postgres only, no Redis: fewer moving parts; Postgres locks give the race
   guarantees.
4. App-level tenant scoping over RLS: simpler, test-proven; RLS noted as
   next step.
5. LLM-phrased replies with guard + template fallback over templates only:
   friendlier text without giving the model authority over facts.
6. Separate approval thread over interrupting the chat thread.

## 8. Left to the builder

Exact prompts, template wording, seed names, UI layout details, Groq model
choice (default `openai/gpt-oss-20b`, configurable; Llama 3.3 was retired by Groq).
