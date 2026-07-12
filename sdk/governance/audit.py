"""Audit log — append-only JSONL record of every governed action.

NemoClaw's governance requires a durable audit trail. Each line is one event:
  {"seq", "ts", "run_id", "risk", "action", "verdict", "detail", "outcome"}

ts is passed IN (the workflow layer stamps it) so this module has no clock
dependency and stays deterministic/testable. Never rewrites history — append
only. Read back with `tail()` for the CLI.
"""
import os
import json

HERE = os.path.dirname(__file__)
DEFAULT_LOG = os.path.join(HERE, "audit.log.jsonl")


class AuditLog:
    def __init__(self, path=DEFAULT_LOG, run_id="run"):
        self.path = path
        self.run_id = run_id
        self._seq = 0

    def record(self, risk, action, verdict, detail=None, outcome=None, ts=None):
        self._seq += 1
        event = {
            "seq": self._seq,
            "ts": ts,                       # ISO string stamped by caller, or None
            "run_id": self.run_id,
            "risk": risk,
            "action": action,
            "verdict": verdict,
            "detail": detail or {},
            "outcome": outcome,
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
        return event

    def tail(self, n=20):
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            lines = f.readlines()
        out = []
        for ln in lines[-n:]:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
        return out
