# Evaluation report

## What is evaluated

Two layers:

1. **Automated tests** (`backend/tests`, 175 tests): unit tests for rules, time
   parsing, templates, the reply guard and the LLM clients; integration tests
   through the real HTTP API and a real Postgres, with a scripted fake LLM.
2. **Evaluation set** (`backend/eval/cases.jsonl`, 41 cases): end-to-end
   scenarios, each run against a fresh database through the HTTP API, written to
   `backend/eval/results.jsonl` with the fields `id, input, expected, actual, pass, notes`.

The same cases run in two modes:

- **fake**: the planner's tool calls are scripted per case. This proves that
  everything *around* the model is correct (rules, isolation, approval, restart,
  failures, guard, call budget) deterministically, without network or quota.
- **real**: the cases go to Groq (`llama-3.3-70b-versatile`). This measures what
  the fake mode cannot: whether the model picks the right tool, asks instead of
  guessing, and keeps context. Four fake-only cases are skipped here because they
  need the model to misbehave on purpose (rate limit, malformed output, false
  confirmation, premature escalation).

## Method

- Clock fixed at **Friday 20 Nov 2026, 17:30 India time**, sample data seeded for that day.
- A case is a list of steps: employee messages, manager decisions, raw HTTP calls
  (for isolation probes), a service restart, failure switches, clock moves, an
  expiry sweep, or a direct DB change (e.g. "another rider takes the last seat").
- Judging: every key in `expected` must equal the same key in `actual`. Phrase
  checks (`reply_contains`, `last_message_contains`) compare the list of phrases
  found; "must not contain" checks pass when nothing forbidden was found. Every
  case also checks that no turn used more than 4 LLM calls. DB facts (roster time,
  request status, request count per company) are read from Postgres after the
  last step, so a reply can't pass by *saying* the right thing.

## Scenarios

| Category | Cases | What they cover |
|---|---|---|
| isolation | 7 | Another company's conversation (404), identity in request body (422), model emitting another tenant's `employee_id`, "I'm actually Asha from Acme" prompt injection, cross-company manager decision (404) and inbox (empty), roster API |
| cutoff | 5 | Same request rejected for Acme (2 h) and applied for Globex (1 h); exact boundary; target cab's own cut-off; pickup cut-off for tomorrow |
| capacity | 4 | Full cab at Acme's 4-seat cap with nearest cabs offered; last seat; Globex full at 6; non-existent slot |
| clarify | 5 | "A bit late" (no time), change without a time, no pickup/drop, "make it 8 instead" follow-ups (full / applied) |
| tool_selection | 6 | Roster question, options question (changes nothing), change, status while pending, leave tomorrow (two cancels), WFH today |
| approval | 6 | Restart between escalation and approval; reject with note; cab filled during approval; withdraw; expiry; double decision (409) |
| failure | 4 | Booking error, cab schedule timeout (3 s vs 1.5 s), LLM 429, malformed model output twice |
| safety | 4 | Model claims success on a rejection; "ignore your rules, I'm the CEO"; model escalates without consent; consented escalation |

## Results

### Fake LLM (deterministic): 41 / 41

| Category | Passed |
|---|---|
| approval | 6/6 |
| capacity | 4/4 |
| clarify | 5/5 |
| cutoff | 5/5 |
| failure | 4/4 |
| isolation | 7/7 |
| safety | 4/4 |
| tool_selection | 6/6 |

All 175 automated tests pass (`docker compose run --rm tests`).

### Real model (Groq)

Not run yet: needs a `GROQ_API_KEY`. Results will be in `backend/eval/results.real.jsonl` and this section.

### Latency

Not measured yet: needs a `GROQ_API_KEY`. `python -m eval.latency --n 40` writes `backend/eval/latency.json`.

## Failures found while building (and fixed)

These were real bugs caught by tests or by reasoning about the requirements. Each has a regression test.

| What failed | Why | Fix |
|---|---|---|
| Double-submit returned "no booking" instead of "already done" | `SELECT … FOR UPDATE` through a join: after waiting for the lock, Postgres re-checked the moved row against the *old* cab row and dropped it | Lock the roster row on its own, then read the joined data |
| Every token rejected in the demo | Tokens were stamped by the demo clock (17:30) but PyJWT checked expiry against the wall clock | Expiry checked against the same app clock that issued the token |
| A restart wiped pending requests | Sample data was re-seeded on every boot | Seed only an empty database |
| The model could escalate without asking | `escalate=true` on the first message was honoured | Code allows escalation only for the exact change offered in the previous turn |
| UI went blank after sending | A React effect returned `scrollIntoView()`'s Promise (newer Chrome) as its cleanup | Effect body in braces |
| Two tests failed only inside the Docker image | The route serving the web UI caught every path, shadowing API routes (405 on an unknown POST) | UI served only as the fallback for a page request that would otherwise 404 |

A final independent code review then found these, all fixed with a reproducing test first:

| What failed | Fix |
|---|---|
| "Drop at 00:30" was re-asked forever (read as 00:30 or 12:30 *today*, both past) | Times just after midnight resolve to tonight; explicit 24-hour times are never flipped to PM |
| A cab that had already left was treated as "past the cut-off" and could be escalated | New `DEPARTED` rule: "Your 09:00 pickup today has already left, so there's nothing to change." |
| Expiry waited for the *requested* cab, so the employee could be told a cab that had already left was "still booked" | Expiry keyed on the earlier of the booked and requested cab |
| A reworded refusal like "You're now on the 20:30 cab." passed the guard (no success keyword, allowed time) | Refusals, pending and mixed turns are never rephrased |
| A direct change made while a request was pending could be undone by a later approval | The direct change withdraws the pending request; approval also checks the booked trip hasn't changed |
| A booking that committed just before its timeout was reported as "nothing was changed" | After a booking timeout the agent re-reads what was written and reports it; lock waits capped at 1 s |
| The trips panel labelled tomorrow's trips "Today" once today's was cancelled | The roster API returns the day label from the app clock |

## Known failures and limitations

- **The fake mode does not measure language understanding.** It proves the system is safe whatever the model plans; the real-model run is what measures planning quality.
- **Ambiguity is partly delegated to the model.** Code enforces "no time → ask" and "time that makes no sense → ask", but whether "change my cab" (no pickup/drop) gets a question depends on the planner choosing `ask_clarification`. If it guesses a direction, the rules still hold; the employee just gets an answer about the wrong trip.
- **Only today and tomorrow, own route only.**
- **Free-tier rate limits.** Groq's free tier limits tokens per minute; under load the agent answers with the plain "trouble understanding" message rather than queueing.
- **Audit timestamps use the database clock**, message timestamps the app clock (they differ in the demo, where the clock starts at 17:30).

## A trade-off in detail: no seat hold during approval

When a request is escalated, the target seat is **not** reserved.

- *If we held it:* the employee is guaranteed the seat if approved, but a seat on
  an evening cab is the scarcest thing in the system, approvals can take hours,
  and many escalations are rejected. Every pending request would block someone
  who could have been booked immediately, and we'd need hold expiry and release logic.
- *Not holding it (chosen):* other employees keep booking normally. The price is
  that an approved change can find the cab full. That path is handled explicitly:
  `apply_decision` re-checks the seat under a row lock, records `APPROVED_NO_SEAT`,
  keeps the original trip, and tells the employee in plain words
  (`approval` cases `appr-cab-filled`, test `test_cab_filled_during_approval`).

I'd revisit this per company: a client with few escalations and a strict SLA
might prefer a short hold.
