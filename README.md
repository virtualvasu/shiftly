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

Open **http://localhost:8000**, scroll to see how it works, and sign in with a demo account
(password for all of them: `cabdesk-demo`).

- Works **without any API key**: a small offline planner (regex) stands in for the LLM.
- For the real agent, copy `.env.example` to `.env` and set `GROQ_API_KEY` (free at console.groq.com), then `docker compose up` again.
- Port 8000 busy? `APP_PORT=8010 docker compose up`.
- The demo clock starts at **17:30 India time** whatever the wall clock says (`DEMO_START` in `.env`), so the evening scenarios always work. "Simulate failures → Reset sample data" in the app re-seeds today's data.

| Email | Who | Try |
|---|---|---|
| `arjun@globex.test` | Globex employee (1 h cut-off) | "move my drop to 7:30 pm" → applied |
| `rohan@acme.test` | Acme employee (2 h cut-off) | same message → rule explained → "yes" → sent to manager |
| `kavita@acme.test` | Acme transport manager | approve / reject Rohan's request |
| `asha@acme.test` | Acme employee | "move my drop to 8 pm" → cab full, nearest free cabs offered |
| `farah@globex.test` | Globex transport manager | sees only Globex requests |

Also `meera@acme.test`, `sneha@globex.test`, `vikram@globex.test`. Passwords are stored as
salted scrypt hashes; the token carries the company and role from the database.

### Tests and evaluation

```bash
docker compose run --rm tests                          # 199 tests, fake LLM, real Postgres
docker compose run --rm tests python -m eval.run       # evaluation set -> backend/eval/results.jsonl
docker compose run --rm tests python -m eval.run --mode real --pause 15   # same cases against Groq (needs key)
docker compose run --rm tests python -m eval.latency --n 40 --pause 15    # p95 latency with the real model
docker compose run --rm tests python -m eval.summary   # compare the stored real-model runs
```

The evaluation set is `backend/eval/cases.jsonl`; results (one line per case with
`id, input, expected, actual, pass, notes`) are in `backend/eval/results.jsonl`.
The report is [docs/evaluation.md](docs/evaluation.md). The full design is in [docs/design.md](docs/design.md).

---

## Evaluation and how it improved

Two questions, measured separately:

1. **Is the system correct whatever the model does?** The 41 cases with a scripted
   model: rules, isolation, approval after restart, failures, races. **41/41**, plus 199 automated tests.
2. **How good is the real model at driving it?** The same cases against Groq
   (37 of them; 4 need a deliberately misbehaving model and only run scripted).

Every real-model run is kept in [`backend/eval/history/`](backend/eval/history/) and
the table below is `python -m eval.summary` over those files:

| Category | Run 1 | Run 2 |
|---|---|---|
| approval | 6/6 | 6/6 |
| capacity | 4/4 | 4/4 |
| clarify | 2/5 | 3/5 |
| cutoff | 1/5 | 4/5 |
| failure | 1/2 | 2/2 |
| isolation | 7/7 | 6/7* |
| safety | 1/2 | 2/2 |
| tool_selection | 4/6 | 4/6 |
| **Total** | **26/37** | **31/37** |

\* not an isolation failure: I ran the unit tests during that run, which reset the shared test database mid-case.

What each run taught, and what changed:

| Before | What the real model did | What I changed | After |
|---|---|---|---|
| First call | Groq had retired Llama 3.3, so every call returned 404. The agent answered "trouble understanding, nothing was changed" as designed. | Compared the three models still served; `openai/gpt-oss-20b` was correct and fastest | — |
| Unpaced run | 24 turns refused with HTTP 429: the free tier allows 8,000 tokens/min (~4 messages/min) | Runs paced at one turn every 15 s; rate-limited replies never count as model quality | — |
| Run 1 | Asked "which drop, today or tomorrow?" and "8 AM or PM?", checked options instead of booking, and once **changed tomorrow's drop** for "the 7:30 pm drop instead" | The day is decided **in code** (next trip that hasn't left) unless the employee says it; prompt gets rules and six worked examples | 26 → 31 |
| Run 2 | Still sometimes set "tomorrow" or "pickup" that the employee never wrote ("cancel my drop" → pickup) | Code grounds the model's choice in the employee's words: an unsaid "tomorrow" is dropped, a contradicted pickup/drop is corrected | covered by scripted tests; not re-run on the real model |

Across all runs the guarantees held: no change was ever applied against a rule,
and no isolation probe leaked. The remaining failures are the model choosing a
weaker but safe action (asking a question it didn't need to, or cancelling one trip
of two); they are listed case by case in [docs/evaluation.md](docs/evaluation.md).

**Latency** (40 real messages, `backend/eval/latency.json`): **p95 1.51 s**, p50 1.07 s,
max 2.90 s, never more than 2 LLM calls. 5 of the 40 were rate-limited by the free tier
and are reported separately, not counted as fast answers.

**Bugs found on the way,** each fixed with a test that failed first: 5 while building
(e.g. a Postgres lock subtlety that made a double-submit look like "no booking"),
1 by checking out the repo fresh (UI routes shadowing API routes inside Docker),
and 7 by an independent code review (e.g. "drop at 00:30" looping on a question,
a departed cab treated as escalatable). Details in [docs/evaluation.md](docs/evaluation.md).

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
 respond   template built from the facts; for successful or info-only turns the LLM
           may rephrase it (last call), and a guard rejects any rephrase that
           invents a time, an id or a success. Refusals are never rephrased.
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
| The reply | Template from facts; LLM may only rephrase successful or info-only turns | A confident wrong confirmation is worse than none |

### Staying under 4 LLM calls and 5 seconds

- Fixed pipeline, not a free agent loop: **plan (1) + optional retry (1) + optional rephrase (1) = 3 max**. A per-message `LLMBudget` raises on a 5th call; every test turn asserts `llm_calls <= 4`. Typical turn: 1–2 calls (refusals and questions: 1; successful changes: 2).
- Time budget per message: planner timeout 2.5 s, each tool 1.5 s (reads retried once; a booking that times out is re-checked so the employee is never told "nothing changed" when it did), rephrase 1.5 s and **skipped when less than 1.5 s of the 4.5 s budget is left** (the template is used). A hard 10 s turn timeout returns a plain message instead of hanging.
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

The cut-off applies to both the booked cab and the target cab. A cab that has
already left can't be changed at all. A full cab or a non-existent slot is never escalated (a manager can't create a seat); the agent
offers the two nearest cabs that pass every rule.

### Manager approval and restarts

Each escalation starts its own LangGraph thread (`approval:<request id>`) that
stops at `interrupt()`; the checkpoint and the request row are both in Postgres.
The manager endpoint looks the request up **within the manager's company**,
resumes that thread with `Command(resume=...)`, the decision is applied (approval
overrides the cut-off and re-checks the seat) and the result is posted into the
employee's conversation, both the message log and the chat graph's memory, so
"ok, then 9?" works next. The same thread is resumed by the employee
("withdraw it") or by an expiry sweeper (no decision 15 min before the earlier of the booked and requested cab:
original trip kept, employee told to call the desk).
If the employee makes a different change themselves while waiting, the pending
request is withdrawn, so a later approval can't undo it.

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
5. **Templates as the source of truth, LLM only rephrases good news.** Refusals, pending requests and mixed turns always use the template, so they can't be reworded into a confirmation. Successful and info-only turns may be rephrased; the guard then rejects any rephrase with a time or request id that isn't in the facts, or a success claim code didn't produce. Replies are a bit less varied; that's the price.
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
