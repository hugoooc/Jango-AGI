"""SDK-based LegacyPilot product: parallel simulation fleet on hai-agents.

The GUI-only computer-use product, rebuilt on H Company's official SDK. A fleet
controller fans simulation variants across worker agents (cloud-concurrent, or
one local desktop per machine), governed by the NemoClaw-pattern layer, and
aggregates the structured results into a trade study.
"""
from .fleet import Fleet, Worker, Variant, RunResult, run_fleet

__all__ = ["Fleet", "Worker", "Variant", "RunResult", "run_fleet"]
