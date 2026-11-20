"""Evaluation runner.

    python -m eval.run                 # fake LLM, deterministic; exit 1 on any failure
    python -m eval.run --mode real     # real Groq model; failures are reported, not hidden
    python -m eval.run --only id1,id2  # a subset

Each case in cases.jsonl runs against a fresh database through the real HTTP
API. Results go to one JSON line per case with exactly these fields:
id, input, expected, actual, pass, notes.

How a case is judged: every key in `expected` must match the same key in
`actual`. `reply_contains` passes when all listed phrases are in the last
reply; `reply_not_contains` / `body_not_contains` list the forbidden phrases
that were found (pass = none found). `max_llm_calls` passes when no turn used
more. Everything else is an exact comparison.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from datetime import date, datetime, timedelta
from pathlib import Path

from app.agent.llm import FakeLLM, GroqClient, InvalidModelOutput, LLMError, PlanResponse, ToolCall
from app.approval.sweeper import expire_stale
from app.clock import IST, FixedClock
from app.config import Settings
from app.db import create_pool, reset_schema
from app.testing import running_app

HERE = Path(__file__).parent
CASES = HERE / "cases.jsonl"
TODAY = date(2026, 11, 20)
DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/shift_test")


def _plans(items: list) -> list:
    out = []
    for it in items:
        if "error" in it:
            out.append(InvalidModelOutput("bad tool call") if it["error"] == "invalid" else LLMError(it["error"]))
        elif "text" in it:
            out.append(PlanResponse([], it["text"]))
        else:
            out.append(PlanResponse([ToolCall(c["name"], c.get("args", {})) for c in it["calls"]]))
    return out


def render_input(steps: list[dict]) -> str:
    parts = []
    for s in steps:
        if "say" in s:
            parts.append(f'{s["as"]}: {s["say"]}')
        elif "decide" in s:
            parts.append(f'{s["as"]}: <{s["decide"]}{" — " + s["note"] if s.get("note") else ""}>')
        elif "http" in s:
            parts.append(f'{s["as"]}: <{s["http"]["method"]} {s["http"]["path"]}>')
        elif s.get("restart"):
            parts.append("<restart service>")
        elif "faults" in s:
            parts.append(f"<faults {json.dumps(s['faults'])}>")
        elif "advance_min" in s:
            parts.append(f"<clock +{s['advance_min']} min>")
        elif s.get("sweep"):
            parts.append("<expiry sweep>")
        elif "sql" in s:
            parts.append(f"<{s.get('label', 'db change')}>")
    return " | ".join(parts)


class Runner:
    def __init__(self, case: dict, mode: str, pause_s: float):
        self.case, self.mode, self.pause_s = case, mode, pause_s
        self.clock = FixedClock(datetime(2026, 11, 20, 17, 30, tzinfo=IST))
        self.tokens: dict[str, str] = {}
        self.conversations: dict[str, str] = {}
        self.last_chat: dict | None = None
        self.last_http: tuple[int, object] | None = None
        self.llm_calls: list[int] = []
        self.latencies: list[float] = []
        self.request_id: str | None = None

    def settings(self) -> Settings:
        return Settings(database_url=DB_URL, seed_on_start=False, demo_start="", sweeper_interval_s=0,
                        llm_provider="fake" if self.mode == "fake" else "groq")

    def llm(self):
        if self.mode == "fake":
            return self._fake
        s = self.settings()
        return GroqClient(s.groq_api_key, s.groq_model)

    async def start(self, stack: AsyncExitStack, *, seed: bool):
        kwargs = {"settings": self.settings(), "clock": self.clock, "llm": self.llm()}
        if seed:
            kwargs["seed_today"] = TODAY
        self.app, self.client = await stack.enter_async_context(running_app(**kwargs))
        self.tokens.clear()

    async def token(self, emp: str) -> str:
        if emp not in self.tokens:
            r = await self.client.post("/api/auth/token", json={"employee_id": emp})
            self.tokens[emp] = r.json()["token"]
        return self.tokens[emp]

    def fill(self, value):
        if isinstance(value, str):
            for emp, conv in self.conversations.items():
                value = value.replace(f"{{conversation:{emp}}}", conv)
            return value.replace("{request}", self.request_id or "SCR-NONE")
        if isinstance(value, dict):
            return {k: self.fill(v) for k, v in value.items()}
        return value

    async def run(self) -> dict:
        pool = await create_pool(DB_URL)
        await reset_schema(pool)
        await pool.close()
        self._fake = FakeLLM(_plans(self.case.get("fake_plans", [])), self.case.get("fake_replies"))
        stack = AsyncExitStack()
        await self.start(stack, seed=True)
        try:
            for step in self.case["steps"]:
                if step.get("restart"):
                    # Stop the whole app (pool, graphs) and boot a new one on the same database.
                    await stack.aclose()
                    stack = AsyncExitStack()
                    await self.start(stack, seed=False)
                else:
                    await self.step(step)
            return await self.observe()
        finally:
            await stack.aclose()

    async def step(self, s: dict):
        services = self.app.state.services
        if "say" in s:
            body = {"message": s["say"]}
            if s["as"] in self.conversations:
                body["conversation_id"] = self.conversations[s["as"]]
            t0 = time.monotonic()
            r = await self.client.post("/api/chat", json=body, headers={"Authorization": f"Bearer {await self.token(s['as'])}"})
            self.latencies.append(time.monotonic() - t0)
            data = r.json()
            self.last_http = (r.status_code, data)
            if r.status_code == 200:
                self.last_chat = data
                self.conversations[s["as"]] = data["conversation_id"]
                self.llm_calls.append(data["llm_calls"])
                for o in data["outcomes"]:
                    if o["data"].get("request_id"):
                        self.request_id = o["data"]["request_id"]
            if self.mode == "real" and self.pause_s:
                await asyncio.sleep(self.pause_s)
        elif "decide" in s:
            body = {"decision": s["decide"]} | ({"note": s["note"]} if s.get("note") else {})
            r = await self.client.post(f"/api/manager/requests/{self.request_id}/decision", json=body,
                                       headers={"Authorization": f"Bearer {await self.token(s['as'])}"})
            self.last_http = (r.status_code, r.json())
        elif "http" in s:
            h = self.fill(s["http"])
            r = await self.client.request(h["method"], h["path"], json=h.get("json"),
                                          headers={"Authorization": f"Bearer {await self.token(s['as'])}"})
            self.last_http = (r.status_code, r.json())
        elif "faults" in s:
            from app.faults import Fault
            for system, f in s["faults"].items():
                services.faults.set(system, Fault(**f))
        elif "advance_min" in s:
            self.clock.advance(s["advance_min"])
        elif s.get("sweep"):
            await expire_stale(services)
        elif "sql" in s:
            async with services.pool.connection() as conn:
                await conn.execute(s["sql"])

    async def observe(self) -> dict:
        exp = self.case["expected"]
        reply = (self.last_chat or {}).get("reply", "")
        outcomes = (self.last_chat or {}).get("outcomes", [])
        act: dict = {}
        pool = self.app.state.services.pool
        for key, want in exp.items():
            if key == "tools":
                act[key] = (self.last_chat or {}).get("tools")
            elif key == "outcome_kinds":
                act[key] = [o["kind"] for o in outcomes]
            elif key == "outcome_kind":
                act[key] = outcomes[-1]["kind"] if outcomes else None
            elif key == "reason_code":
                act[key] = outcomes[-1]["data"].get("reason_code") if outcomes else None
            elif key == "reply_contains":
                act[key] = [p for p in want if p in reply]
            elif key == "reply_not_contains":
                act[key] = [p for p in want if p in reply]
            elif key == "http_status":
                act[key] = self.last_http[0] if self.last_http else None
            elif key == "decision_status":
                act[key] = (self.last_http[1] or {}).get("status") if self.last_http else None
            elif key == "body_equals":
                act[key] = self.last_http[1] if self.last_http else None
            elif key == "body_not_contains":
                text = json.dumps(self.last_http[1]) if self.last_http else ""
                act[key] = [p for p in want if p in text]
            elif key == "max_llm_calls":
                act[key] = max(self.llm_calls, default=0)
            elif key in ("request_status", "request_count", "roster", "last_message_contains"):
                async with pool.connection() as conn:
                    act[key] = await self._db_fact(conn, key, want)
        act["reply"] = reply
        return act

    async def _db_fact(self, conn, key, want):
        q = lambda sql, *a: conn.execute(sql, a)  # noqa: E731
        if key == "request_status":
            row = await (await q("SELECT status FROM shift_change_requests ORDER BY created_at DESC, id DESC LIMIT 1")).fetchone()
            return row["status"] if row else None
        if key == "request_count":
            return {c: (await (await q("SELECT count(*) AS n FROM shift_change_requests WHERE company_id=%s", c)).fetchone())["n"]
                    for c in want}
        if key == "roster":
            out = {}
            for spec in want:
                emp, day, direction = spec.split()
                d = TODAY if day == "today" else TODAY + timedelta(days=1)
                row = await (await q(
                    "SELECT t.scheduled_at FROM roster r JOIN trips t ON t.id=r.trip_id WHERE r.employee_id=%s "
                    "AND r.trip_date=%s AND r.direction=%s AND r.status='active'", emp, d, direction)).fetchone()
                out[spec] = row["scheduled_at"].astimezone(IST).strftime("%H:%M") if row else "cancelled"
            return out
        if key == "last_message_contains":
            out = {}
            for emp, phrases in want.items():
                row = await (await q(
                    "SELECT m.text FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                    "WHERE c.employee_id=%s AND m.role='assistant' ORDER BY m.id DESC LIMIT 1", emp)).fetchone()
                text = row["text"] if row else ""
                out[emp] = [p for p in phrases if p in text]
            return out


def judge(expected: dict, actual: dict) -> list[str]:
    problems = []
    for key, want in expected.items():
        got = actual.get(key)
        if key in ("reply_not_contains", "body_not_contains"):
            ok = got == []
        elif key == "max_llm_calls":
            ok = got is not None and got <= want
        else:
            ok = got == want
        if not ok:
            problems.append(f"{key}: expected {want!r}, got {got!r}")
    return problems


async def run_all(mode: str, only: set[str] | None, pause_s: float) -> list[dict]:
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    results = []
    for case in cases:
        if only and case["id"] not in only:
            continue
        if mode not in case.get("modes", ["fake", "real"]):
            continue
        expected = {"max_llm_calls": 4, **case["expected"]}
        runner = Runner({**case, "expected": expected}, mode, pause_s)
        try:
            actual = await runner.run()
            problems = judge(expected, actual)
        except Exception as e:  # a crashing case is a failing case, not a crashed run
            actual, problems = {"error": f"{type(e).__name__}: {e}"}, ["case crashed"]
        notes = f"[{case['category']}] {case.get('description', '')}".strip()
        if runner.latencies and mode == "real":
            notes += f" | latency max {max(runner.latencies):.2f}s"
        if problems:
            notes += " | FAIL: " + "; ".join(problems)
        results.append({"id": case["id"], "input": render_input(case["steps"]), "expected": expected,
                        "actual": actual, "pass": not problems, "notes": notes})
        print(f"{'PASS' if not problems else 'FAIL'}  {case['id']}", flush=True)
    return results


def write_results(results: list[dict], out: Path) -> None:
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["fake", "real"], default="fake")
    p.add_argument("--out")
    p.add_argument("--only")
    p.add_argument("--pause", type=float, default=3.0, help="seconds between real LLM turns (free-tier limits)")
    a = p.parse_args(argv)
    out = Path(a.out or HERE / ("results.jsonl" if a.mode == "fake" else "results.real.jsonl"))
    results = asyncio.run(run_all(a.mode, set(a.only.split(",")) if a.only else None, a.pause))
    write_results(results, out)

    by_cat: dict[str, list[bool]] = {}
    for r in results:
        by_cat.setdefault(r["notes"].split("]")[0].strip("["), []).append(r["pass"])
    for cat, passes in sorted(by_cat.items()):
        print(f"{cat:16} {sum(passes)}/{len(passes)}")
    total = sum(r["pass"] for r in results)
    print(f"{'TOTAL':16} {total}/{len(results)}  -> {out}")
    return 0 if a.mode == "real" or total == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
