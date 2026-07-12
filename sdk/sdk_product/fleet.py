"""Fleet controller — run many simulation variants in parallel via the hai-agents SDK.

The product goal: launch multiple agent instances to run simulations concurrently
and aggregate results faster than one-at-a-time.

Parallelism model (verified against the SDK, see SDK_PRODUCT.md):
  - LOCAL DESKTOP is one-per-machine: `BridgeManager` raises
    "cannot serve two local desktop environments from one machine". A single Mac
    can drive exactly one OpenVSP. So local desktop fan-out is physically bounded
    to 1 — you scale it by adding WORKER MACHINES, each running one bridge.
  - CLOUD sessions (browser, or a remote desktop worker) have no such limit:
    `AsyncClient` + `asyncio.gather` run N sessions truly concurrently.

This controller expresses both. You give it a batch of variants and a `Worker`
pool; it dispatches each variant to a free worker, runs them concurrently up to
the pool size, and aggregates the structured answers. With a 1-machine local
pool it runs sequentially (correct, just not parallel); with an N-worker cloud
or multi-machine pool it runs N-wide — same code, the parallelism scales with
the pool, not the source.

Every dispatch is gated by the NemoClaw-pattern governance layer (policy +
approval + egress + audit) from ../governance.
"""
import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hai_agents import AsyncClient, async_run_session  # noqa: E402

from governance import govern, PolicyDenied  # noqa: E402


@dataclass
class Variant:
    """One simulation to run: a name and a plain-language task for the agent."""
    name: str
    task: str
    params: dict = field(default_factory=dict)


@dataclass
class Worker:
    """A capacity slot the controller can dispatch a variant to.

    build_agent(variant) -> an hai_agents Agent (cloud or user_device desktop).
    A local-desktop worker MUST set is_local_desktop=True so the controller
    serializes it (one machine = one bridge); cloud workers run concurrently.
    """
    id: str
    build_agent: Callable[[Variant], Any]
    tools: Optional[list] = None
    is_local_desktop: bool = False
    max_steps: int = 40
    max_time_s: float = 900.0


@dataclass
class RunResult:
    variant: str
    worker: str
    ok: bool
    answer: Any = None
    error: Optional[str] = None


class Fleet:
    """Dispatch a batch of variants across a worker pool, concurrently.

    Concurrency is min(len(cloud workers), batch). Local-desktop workers are
    forced to concurrency 1 (the SDK enforces one bridge per machine anyway);
    the controller respects that so it never tries to start two.
    """

    def __init__(self, api_key, posture="standard", approver=None, clock=None):
        self.api_key = api_key
        self.client = AsyncClient(api_key=api_key)
        self.gov = govern(posture, approver=approver, clock=clock)

    async def _run_one(self, worker: Worker, variant: Variant, sem: asyncio.Semaphore) -> RunResult:
        async with sem:
            try:
                # governance gate: dispatching a sim run is a 'navigate'-class action;
                # mutating geometry happens inside via approved tools.
                self.gov.check("navigate", "dispatch_variant",
                               {"worker": worker.id, "variant": variant.name})
            except PolicyDenied as e:
                return RunResult(variant.name, worker.id, ok=False, error=f"policy: {e}")

            agent = worker.build_agent(variant)
            try:
                result = await async_run_session(
                    self.client, agent=agent, messages=variant.task,
                    tools=worker.tools, max_steps=worker.max_steps,
                    max_time_s=worker.max_time_s, timeout_seconds=worker.max_time_s + 120,
                )
                return RunResult(variant.name, worker.id, ok=True,
                                 answer=getattr(result, "answer", None))
            except Exception as e:
                return RunResult(variant.name, worker.id, ok=False, error=f"{type(e).__name__}: {e}")

    async def run_batch(self, variants: list[Variant], workers: list[Worker]) -> list[RunResult]:
        """Assign variants round-robin to workers and run concurrently.

        Concurrency = number of workers, except that all local-desktop workers
        share a single slot (one machine, one bridge)."""
        if not workers:
            raise ValueError("no workers")
        has_local = any(w.is_local_desktop for w in workers)
        cloud_workers = [w for w in workers if not w.is_local_desktop]
        # local desktop forces serialization; cloud scales to the pool size
        concurrency = 1 if has_local and not cloud_workers else max(1, len(cloud_workers) or 1)
        sem = asyncio.Semaphore(concurrency)

        tasks = []
        for i, v in enumerate(variants):
            w = workers[i % len(workers)]
            tasks.append(self._run_one(w, v, sem))
        return await asyncio.gather(*tasks)


def run_fleet(api_key, variants, workers, posture="standard", approver=None, clock=None):
    """Sync entry point: run a batch and return results."""
    fleet = Fleet(api_key, posture=posture, approver=approver, clock=clock)
    return asyncio.run(fleet.run_batch(variants, workers))
