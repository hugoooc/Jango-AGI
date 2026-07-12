"""Parser — natural-language design question -> a structured Request.

Uses one small text LLM call (Holo `ask`, no image) constrained to the registry
vocabulary, with a deterministic regex fallback so a parse never hard-fails on
the simple cases. The agent layer can also build a Request directly and skip
this entirely.

Examples it handles:
  "what's the impact on mass if I change the wingspan to 12m?"
  "increase wing span by 10% and tail area by 5%, how does CG and mass change?"
  "set wing chord to 4, report wetted area and volume"
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cu"))
import desktop as d          # noqa: E402

from . import registry as R  # noqa: E402
from .dispatcher import Change, Request  # noqa: E402


def parse(question: str) -> Request:
    """Parse a question into a Request. Tries the LLM, falls back to regex."""
    req = _llm_parse(question)
    if req and req.changes and req.outputs:
        return req
    return _regex_parse(question)


def _llm_parse(question):
    inputs = {k: v["aliases"] for k, v in R.INPUTS.items()}
    outputs = {k: v["aliases"] for k, v in R.OUTPUTS.items()}
    prompt = (
        "You convert an aircraft-design question into JSON. "
        f"Valid input variables (key: aliases): {inputs}. "
        f"Valid outputs (key: aliases): {outputs}. "
        "Return ONLY JSON: {\"changes\":[{\"input\":<key>,\"mode\":\"set|scale|delta\","
        "\"amount\":<number>}], \"outputs\":[<key>,...]}. "
        "mode 'set' = absolute value; 'scale' = fractional change (10% -> 0.10, "
        "-20% -> -0.20); 'delta' = additive. "
        f"Question: {question}"
    )
    try:
        ans = d.ask(prompt, image=None, max_tokens=500)
    except RuntimeError as exc:
        if "Holo authentication failed" in str(exc) or "Holo API key missing" in str(exc):
            raise
        return None
    except Exception:
        return None
    m = re.search(r"\{.*\}", ans or "", re.S)
    if not m:
        return None
    try:
        doc = json.loads(m.group(0))
    except Exception:
        return None
    changes = []
    for c in doc.get("changes", []):
        key = c.get("input") if c.get("input") in R.INPUTS else R.resolve_input(str(c.get("input", "")))
        if key:
            changes.append(Change(key, c.get("mode", "set"), float(c.get("amount", 0))))
    outputs = [o if o in R.OUTPUTS else R.resolve_output(str(o)) for o in doc.get("outputs", [])]
    outputs = [o for o in outputs if o]
    return Request(changes, outputs) if (changes or outputs) else None


_PCT = re.compile(r"(increase|decrease|reduce|\+|-)?\s*([a-z ]+?)\s*(by|to)\s*(-?\d+\.?\d*)\s*(%|m|meters|metres)?", re.I)


def _regex_parse(question):
    """Deterministic fallback for simple 'change X to/by N' phrasings."""
    q = question.lower()
    changes = []
    for m in _PCT.finditer(q):
        direction, var, verb, num, unit = m.groups()
        key = R.resolve_input(var.strip())
        if not key:
            continue
        num = float(num)
        if verb == "to":
            changes.append(Change(key, "set", num))
        else:  # by
            frac = num / 100.0 if unit == "%" else num
            if direction and direction.strip() in ("decrease", "reduce", "-"):
                frac = -abs(frac)
            changes.append(Change(key, "scale" if unit == "%" else "delta", frac))
    outputs = [k for k in R.OUTPUTS if any(a in q for a in R.OUTPUTS[k]["aliases"])]
    return Request(changes, outputs or ["mass"])
