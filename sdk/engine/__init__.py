"""engine — registry-driven GUI design assistant for OpenVSP.

An agent answers arbitrary aircraft-design questions by composing entries from a
registry of INPUTS (variables it can change) and OUTPUTS (quantities it can
measure), all driven through the GUI (never OpenVSP's API). A natural-language
parser turns a question into a Request; the dispatcher runs only the analyses
needed and reports before/after/delta.

  from engine import ask
  print(ask("impact on mass and CG if wingspan changes to 12m?"))
"""
from . import registry, executor, dispatcher, parser
from .dispatcher import Change, Request, run
from .parser import parse


def ask(question, fast_only=True):
    """Parse a question, run it, return the report dict."""
    req = parse(question)
    req.fast_only = fast_only
    return run(req)


__all__ = ["ask", "parse", "run", "Change", "Request",
           "registry", "executor", "dispatcher", "parser"]
