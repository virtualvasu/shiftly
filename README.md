# Shift-Change Request Agent

A chat agent that handles office-cab shift changes end to end: it understands the
request, reads only the caller's roster, applies that company's rules in code,
then makes the change, explains why not and offers the next cab, or escalates to the
transport manager and resumes when the decision arrives, even after a restart.

Stack: Python 3.12, FastAPI, LangGraph, Postgres, JWT · React + shadcn/ui · Groq (free tier).

---

## Run it

```bash
docker compose up
```

Open **http://localhost:8000** and pick a person to sign in as.

- Works **without any API key**: a small offline planner (regex) stands in for the LLM.
- For the real agent, copy `.env.example` to `.env` and set `GROQ_API_KEY` (free at console.groq.com), then `docker compose up` again.
- Port 8000 busy? `APP_PORT=8010 docker compose up`.
- The demo clock starts at **17:30 India time** whatever the wall clock says (`DEMO_START` in `.env`), so the evening scenarios always work. "Simulate failures → Reset sample data" in the app re-seeds today's data.

| Person | Company | Try |
|---|---|---|
| Arjun `G-E01` | Globex (1 h cut-off) | "move my drop to 7:30 pm" → applied |
| Rohan `A-E02` | Acme (2 h cut-off) | same message → rule explained → "yes" → sent to manager |
| Kavita `A-M01` | Acme manager | approve / reject Rohan's request |
| Asha `A-E01` | Acme | "move my drop to 8 pm" → cab full, nearest free cabs offered |

### Tests and evaluation

```bash
docker compose run --rm tests                          # 164 tests, fake LLM, real Postgres
docker compose run --rm tests python -m eval.run       # evaluation set -> backend/eval/results.jsonl
docker compose run --rm tests python -m eval.run --mode real   # same cases against Groq (needs key)
docker compose run --rm tests python -m eval.latency --n 40    # p95 latency with the real model
```

The evaluation set is `backend/eval/cases.jsonl`; results (one line per case with
`id, input, expected, actual, pass, notes`) are in `backend/eval/results.jsonl`.
The report is [docs/evaluation.md](docs/evaluation.md). The full design is in [docs/design.md](docs/design.md).

---

## How it works

**The LLM understands and talks. Code decides.**

```
POST /api/chat  (JWT -> company, employee)
   │
   ▼
 plan      LLM call 1: reads the message + history, returns tool calls (or a question)
   │       invalid output -> one retry with the error (LLM call 2), then "could you rephrase"
 validate  code: schema check, identity args rejected, time resolved, "ask, don't guess",
   │             escalation only if it was offered and accepted
 execute   code: tenant-scoped tools -> rule engine -> one DB transaction
   │
 respond   template built from the facts; LLM may rephrase it (last call),
           a guard rejects any rephrase that invents a time, an id or a success
           -> applied | rejected + next cabs | question | sent to manager (⏸)

manager decision ──▶ approval workflow (own LangGraph thread, Postgres checkpoint)
                     interrupt ⏸ -> apply (re-checks seats) -> message in the same chat
```

### Where the LLM is used, and where plain code is better

| Job | Who does it | Why |
|---|---|---|
| Understanding "can I go a bit late?", "make it 8 instead", "on leave tomorrow" | LLM (planner) | Open-ended language; this is what it's good at |
| Choosing the tool | LLM, validated by code | Measured in the eval; wrong/extra args are rejected |
| Who is asking, which company | JWT only | Never from the body, never from the model |
| Cut-off, seats, same route, today/tomorrow | `rules.py` (pure functions) | Must be exact and identical every time |
| Applying the change | `booking.py` (one transaction, row locks) | Correctness under concurrency |
| Whether to escalate | Code checks the employee said yes to *this* offer | Model can't escalate on its own |
| Times like "8" next to a 19:00 drop | `timeparse.py` | Deterministic; unclear times become a question |
| The reply | Template from facts; LLM may only rephrase | A confident wrong confirmation is worse than none |

### Staying under 4 LLM calls and 5 seconds

- Fixed pipeline, not a free agent loop: **plan (1) + optional retry (1) + optional rephrase (1) = 3 max**. A per-message `LLMBudget` raises on a 5th call; every test turn asserts `llm_calls <= 4`. Typical turn: 2 calls; clarifying questions: 1.
- Time budget per message: planner timeout 2.5 s, each tool 1.5 s (reads retried once), rephrase 1.5 s and **skipped when less than 1.5 s of the 4.5 s budget is left** (the template is used). A hard 10 s turn timeout returns a plain message instead of hanging.
- Groq's free tier is fast enough for this pipeline. The measured p95 is in [docs/evaluation.md](docs/evaluation.md).

### Multi-company isolation

- `TenantContext(company_id, employee_id, role)` is built only from the verified JWT and passed explicitly to every repository function; every tenant query filters by `company_id`. There is no unscoped query for tenant data.
- Request bodies are `extra="forbid"`: a body with `company_id` is a 422.
- Tool argument schemas contain only business fields (day, pickup/drop, time); a model emitting `employee_id`/`company_id` makes the plan invalid.
- Cross-tenant lookups return 404 (no existence leak). Cabs belong to a company, so seat counts never mix.
- Proven by tests: `test_repo_isolation.py`, `test_auth.py`, `test_chat_api.py::test_foreign_conversation_404`, `test_chat_flow.py::test_model_identity_args_never_reach_tools`, `test_approval.py::test_other_company_manager_404`, and the 7 `iso-*` eval cases.

### Rules (sample data)

| | Acme Tech | Globex Corp |
|---|---|---|
| Drop changes, at least | 2 h before the cab | 1 h |
| Pickup changes, at least | 4 h | 2 h |
| Seats filled per cab (policy cap on a 6-seat vehicle) | 4 | 6 |
| Manager may override the cut-off | yes | yes |

The cut-off applies to both the booked cab and the target cab. A full cab or a
non-existent slot is never escalated (a manager can't create a seat); the agent
offers the two nearest cabs that pass every rule.

### Manager approval and restarts

Each escalation starts its own LangGraph thread (`approval:<request id>`) that
stops at `interrupt()`; the checkpoint and the request row are both in Postgres.
The manager endpoint looks the request up **within the manager's company**,
resumes that thread with `Command(resume=...)`, the decision is applied (approval
overrides the cut-off and re-checks the seat) and the result is posted into the
employee's conversation, both the message log and the chat graph's memory, so
"ok, then 9?" works next. The same thread is resumed by the employee
("withdraw it") or by an expiry sweeper (no decision 15 min before the cab:
original trip kept, employee told to call the desk).

Restart proof: `test_approval.py::test_restart_then_manager_approves` shuts the
app down, boots a new one on the same DB, asserts the workflow is still paused at
`await_decision`, approves, and checks the message in the same conversation.
Also checked manually with `docker compose restart api` between escalation and approval.

### When things go wrong

| Situation | What the employee sees |
|---|---|
| Roster / cab schedule / booking times out or errors | "I couldn't reach the cab schedule just now, so nothing was changed. Your 19:00 drop is still booked. Please try again in a minute, or call the transport desk if it's urgent." |
| LLM down / rate-limited | "I'm having trouble understanding messages right now. Nothing was changed. …" |
| Model output malformed twice | "Sorry, I didn't quite get that. Could you tell me the time, and whether it's your pickup or drop?" |
| Cab filled up during approval | "Your manager approved request SCR-…, but the 20:30 cab filled up in the meantime. Your 19:00 drop is still booked. …" |
| Stuck in approval | Status shows when it was sent; expiry message before the cab |
| Double-clicked send | 409 "Still working on your last message." (one turn per conversation, Postgres advisory lock) |
| Anything unexpected | Plain message + reference id; the trace stays in the server log |

The "Simulate failures" panel in the UI switches these on for the demo.

### Bonus: last seat and double-submit

`booking.py` locks the employee's roster row, then the cab rows in ascending id
order (no deadlocks), re-reads occupancy under the lock, then writes. Two people
racing for the last seat: exactly one gets it, the other gets "full" and the
nearest cabs. The same change submitted twice: the second sees it already done
and returns the same request id; pending escalations are also protected by a
partial unique index. Proven by `test_concurrency.py` with real concurrent
transactions on Postgres (run repeatedly while building).

---

## Design decisions and trade-offs

1. **Planner + executor instead of a free ReAct loop.** I lose some flexibility (the model can't chain arbitrary tool calls), and I gain a hard call cap, predictable latency, and facts that never come from the model. For a desk that must not quote the wrong rule, that is the right trade.
2. **No seat hold while waiting for the manager.** Approvals can take hours and seats are scarce; holding one would block other employees for a request that may be rejected. The cost is that an approved change can find the cab full, so that path is handled and tested explicitly ("filled up in the meantime").
3. **Postgres only, no Redis.** One store gives row locks, a unique index for idempotency, and the LangGraph checkpointer. Fewer moving parts to run and explain.
4. **Separate approval thread instead of pausing the chat thread.** An approval can take hours; pausing the chat would block every other message in it. The pause is still a real LangGraph interrupt, and the decision still lands in the same conversation.
5. **Templates as the source of truth, LLM only rephrases.** Replies are a bit less varied, and a false confirmation can't get through: the guard rejects any rephrase with a success word that code didn't produce, or a time or request id that isn't in the facts.
6. **App-level tenant scoping instead of Postgres row-level security.** Simpler to read and change, and proven by tests. RLS would be my next step for defence in depth.
7. **Escalation needs consent, enforced in code.** If the model sets `escalate=true` before the employee was told the rule and said yes, code ignores it and returns the explanation first.

## Limitations and next steps

- Only today's and tomorrow's trips, only moves within the employee's own route. Address or route changes are out of scope.
- No notification channel besides the chat (the UI polls every 3 s).
- The offline planner is deliberately basic; it exists so the app runs without a key.
- Audit log timestamps use the database clock, message timestamps the app (demo) clock.
- Next: RLS, waitlist notification when an approved change frees a seat, per-company escalation SLAs.

## Layout

```
backend/app/
  auth.py            JWT, TenantContext
  rules.py           cut-off and capacity rules (pure)
  booking.py         the only roster writer: locks, idempotency, decisions
  repo.py            tenant-scoped SQL
  agent/             planner prompt, tools, LLM clients, graph, templates, guard
  approval/          approval workflow (interrupt/resume) and expiry sweeper
  api/               HTTP routes and error handling
backend/tests/       pytest (fake LLM, real Postgres)
backend/eval/        cases.jsonl, runner, results, latency benchmark
frontend/            React + Vite + shadcn/ui
docs/                design.md, evaluation.md
```
