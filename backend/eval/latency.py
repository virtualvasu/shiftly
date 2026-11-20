"""Measure reply latency with the real LLM.

    python -m eval.latency --n 40

Sends a mix of realistic messages through POST /api/chat (in-process app,
real Groq model, real Postgres) and records wall time per reply. Each message
starts a fresh conversation; the sample data is reset every round so changes
don't pile up. Pauses between calls (to respect free-tier rate limits) are
not counted.
"""

import argparse
import asyncio
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

from app.agent import templates
from app.clock import IST, FixedClock
from app.config import Settings
from app.db import create_pool, reset_schema
from app.seed import seed
from app.testing import running_app
from eval.run import DB_URL, TODAY

OUT = Path(__file__).with_name("latency.json")

MESSAGES = [
    ("G-E01", "move my drop to 7:30 pm"),
    ("A-E02", "can I take the 7:30 drop instead of 7?"),
    ("A-E01", "which cab am I on tonight?"),
    ("G-E02", "can I go a bit late?"),
    ("A-E03", "I'm on leave tomorrow"),
    ("G-E03", "what cabs are there around 9 tonight?"),
    ("A-E01", "push my drop to 9:30 pm please"),
    ("G-E01", "change my pickup tomorrow to 8 am"),
    ("A-E02", "any update on my request?"),
    ("G-E02", "working from home today, cancel my drop"),
]


def summarize(samples: list[tuple[float, int, bool]]) -> dict:
    """samples: (seconds, llm_calls, model_unavailable).

    Replies where the model was unavailable (e.g. free-tier rate limit) come back
    fast with a fallback message; they are counted separately so they can't make
    the p95 look better than the model really is.
    """
    answered = [s for s in samples if not s[2]]
    times = [s[0] for s in answered]
    return {
        "answered": len(answered),
        "model_unavailable": len(samples) - len(answered),
        "p50_s": round(percentile(times, 50), 3),
        "p95_s": round(percentile(times, 95), 3),
        "max_s": round(max(times), 3),
        "max_llm_calls": max(s[1] for s in samples),
        "mean_llm_calls": round(sum(s[1] for s in answered) / len(answered), 2),
    }


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


async def measure(n: int, pause_s: float) -> dict:
    settings = Settings(database_url=DB_URL, seed_on_start=False, demo_start="", sweeper_interval_s=0, llm_provider="groq")
    if not settings.groq_api_key:
        raise SystemExit("GROQ_API_KEY is not set; latency needs the real model.")
    pool = await create_pool(DB_URL)
    await reset_schema(pool)
    await pool.close()

    clock = FixedClock(datetime(2026, 11, 20, 17, 30, tzinfo=IST))
    samples = []
    async with running_app(settings=settings, clock=clock, seed_today=TODAY) as (app, c):
        tokens = {}
        for i in range(n):
            if i and i % len(MESSAGES) == 0:
                await seed(app.state.services.pool, TODAY)
            emp, text = MESSAGES[i % len(MESSAGES)]
            if emp not in tokens:
                tokens[emp] = (await c.post("/api/auth/token", json={"employee_id": emp})).json()["token"]
            t0 = time.monotonic()
            r = await c.post("/api/chat", json={"message": text}, headers={"Authorization": f"Bearer {tokens[emp]}"})
            body = r.json()
            sample = (time.monotonic() - t0, body.get("llm_calls", 0), body.get("reply") == templates.llm_failure())
            samples.append(sample)
            flag = "  (model unavailable)" if sample[2] else ""
            print(f"{sample[0]:5.2f}s  {sample[1]} calls  {emp}: {text}{flag}", flush=True)
            await asyncio.sleep(pause_s)
    return {"n": n, "model": settings.groq_model, **summarize(samples)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--pause", type=float, default=3.0)
    a = p.parse_args(argv)
    result = asyncio.run(measure(a.n, a.pause))
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
