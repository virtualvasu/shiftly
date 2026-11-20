"""Timeouts, retries and switchable failures around every tool call.

The fault injector lets tests (and the demo) make the roster, the cab schedule
or the booking system slow or broken, to show how failures reach the employee.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, TypeVar

log = logging.getLogger("app.tools")

System = Literal["roster", "cabs", "booking"]
T = TypeVar("T")


@dataclass
class Fault:
    latency_s: float = 0
    fail: bool = False


class FaultInjector:
    def __init__(self) -> None:
        self._faults: dict[str, Fault] = {}

    def set(self, system: System, fault: Fault) -> None:
        self._faults[system] = fault

    def clear(self) -> None:
        self._faults.clear()

    def get(self, system: str) -> Fault:
        return self._faults.get(system, Fault())

    def snapshot(self) -> dict:
        return {k: vars(v) for k, v in self._faults.items()}


class ToolFailure(Exception):
    def __init__(self, system: str, kind: Literal["timeout", "error"]):
        super().__init__(f"{system} {kind}")
        self.system = system
        self.kind = kind


async def call_tool(
    system: str,
    fn: Callable[[], Awaitable[T]],
    faults: FaultInjector,
    *,
    timeout_s: float = 1.5,
    retries: int = 0,
) -> T:
    """Run fn with a timeout. Read-only callers pass retries=1; writes never retry."""

    async def attempt() -> T:
        fault = faults.get(system)
        if fault.latency_s:
            await asyncio.sleep(fault.latency_s)
        if fault.fail:
            raise RuntimeError(f"injected failure in {system}")
        return await fn()

    kind: Literal["timeout", "error"] = "error"
    for i in range(retries + 1):
        try:
            return await asyncio.wait_for(attempt(), timeout_s)
        except asyncio.TimeoutError:
            kind = "timeout"
            log.warning("tool %s timed out (attempt %d)", system, i + 1)
        except Exception:
            kind = "error"
            log.exception("tool %s failed (attempt %d)", system, i + 1)
    raise ToolFailure(system, kind)
