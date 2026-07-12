"""Policy engine — NemoClaw-pattern action governance.

Every action LegacyPilot's orchestrator takes is classified into a risk class
and evaluated against a *posture profile*. The result is a verdict:

  ALLOW    proceed (audited)
  APPROVE  proceed only after an operator confirms (audited either way)
  DENY     refuse; raise PolicyDenied (audited)

Posture profiles mirror NemoClaw's risk framework:

  strict      mutate + destructive + egress all require approval
  standard    destructive requires approval; mutate/egress allowed
  permissive  everything allowed (still audited)

Risk classes (see governance/README.md):
  read         screenshot / read a field or result value
  navigate     open a geom, click a tab, open a dialog
  mutate       set a geometry parameter
  destructive  save/overwrite/delete/quit-with-unsaved-changes
  egress       outbound model/API call (Holo)
"""
import os
import json

HERE = os.path.dirname(__file__)

# verdicts
ALLOW = "allow"
APPROVE = "approve"
DENY = "deny"

RISK_CLASSES = ("read", "navigate", "mutate", "destructive", "egress")

# built-in posture profiles: risk class -> verdict
POSTURES = {
    "strict": {
        "read": ALLOW, "navigate": ALLOW,
        "mutate": APPROVE, "destructive": APPROVE, "egress": APPROVE,
    },
    "standard": {
        "read": ALLOW, "navigate": ALLOW,
        "mutate": ALLOW, "destructive": APPROVE, "egress": ALLOW,
    },
    "permissive": {
        "read": ALLOW, "navigate": ALLOW,
        "mutate": ALLOW, "destructive": ALLOW, "egress": ALLOW,
    },
}


class PolicyDenied(Exception):
    """Raised when policy denies an action outright."""


class Policy:
    """A resolved policy: a posture profile + an egress allowlist + optional
    per-class overrides. Immutable after construction."""

    def __init__(self, name, rules, egress_allow=None, meta=None):
        self.name = name
        self.rules = dict(rules)               # risk class -> verdict
        self.egress_allow = list(egress_allow or [])
        self.meta = dict(meta or {})

    def verdict(self, risk_class):
        """Verdict for a risk class (defaults to DENY for unknown classes —
        fail closed)."""
        return self.rules.get(risk_class, DENY)

    def egress_allowed(self, host):
        """True if `host` matches the egress allowlist (suffix match, so
        'api.hcompany.ai' matches an allowlist entry 'hcompany.ai')."""
        h = (host or "").lower()
        return any(h == a or h.endswith("." + a) or h.endswith(a)
                   for a in self.egress_allow)

    def summary(self):
        return {"policy": self.name, "rules": self.rules,
                "egress_allow": self.egress_allow, **self.meta}


def load_policy(name_or_path="standard"):
    """Load a posture by name ('strict'/'standard'/'permissive') or from a JSON
    file. A file may set: {"posture": "standard", "overrides": {...},
    "egress_allow": [...]}."""
    if name_or_path in POSTURES:
        return Policy(name_or_path, POSTURES[name_or_path],
                      egress_allow=_default_egress())
    # else: treat as a path to a JSON policy file
    path = name_or_path
    if not os.path.isabs(path) and not os.path.exists(path):
        path = os.path.join(HERE, name_or_path)
    with open(path) as f:
        doc = json.load(f)
    base = dict(POSTURES.get(doc.get("posture", "standard"), POSTURES["standard"]))
    base.update(doc.get("overrides", {}))
    return Policy(doc.get("name", os.path.basename(path)), base,
                  egress_allow=doc.get("egress_allow", _default_egress()),
                  meta={"posture": doc.get("posture", "standard")})


def _default_egress():
    """Default egress allowlist: only the Holo vision endpoint the GUI driver
    legitimately needs. Everything else is off by default (fail closed)."""
    return ["api.hcompany.ai"]
