"""SDK-based LegacyPilot product: parallel simulation fleet on hai-agents.

The GUI-only computer-use product, rebuilt on H Company's official SDK. A fleet
controller fans simulation variants across worker agents (cloud-concurrent, or
one local desktop per machine), governed by the NemoClaw-pattern layer, and
aggregates the structured results into a trade study.
"""
__all__ = ["Fleet", "Worker", "Variant", "RunResult", "run_fleet"]


def __getattr__(name):
    """Load the H SDK only when a fleet symbol is requested.

    Voice transcription and the localhost UI do not need the fleet runtime.
    """
    if name in __all__:
        from . import fleet

        return getattr(fleet, name)
    raise AttributeError(name)
