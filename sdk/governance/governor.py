"""Governor — the single gate every governed action passes through.

Ties policy + approval + audit together. This is the out-of-process-style
enforcement point (NemoClaw's "policy enforcement" role): the orchestrator does
not act directly; it asks the Governor, which either lets it proceed, requires
operator approval, or denies — and records the outcome either way.

    gov = Governor(load_policy("standard"))
    gov.check("mutate", "set_field",
              {"geom": "Wing", "field": "Span", "before": 38.4, "after": 43.0})
    # -> returns None (allowed), or raises PolicyDenied

Egress is a special case: check the destination host against the allowlist.
"""
from urllib.parse import urlparse

from .policy import Policy, PolicyDenied, ALLOW, APPROVE, DENY
from .audit import AuditLog
from .approval import Approver, ConsoleApprover


class Governor:
    def __init__(self, policy, approver=None, audit=None, clock=None):
        self.policy = policy
        self.approver = approver or ConsoleApprover()
        self.audit = audit or AuditLog()
        # clock() -> ISO timestamp string; injected so this module has no
        # nondeterministic time dependency. None => timestamps are null.
        self._clock = clock

    def _ts(self):
        try:
            return self._clock() if self._clock else None
        except Exception:
            return None

    def check(self, risk, action, detail=None):
        """Gate one action. Returns None if permitted; raises PolicyDenied if
        refused. Always writes an audit record."""
        detail = detail or {}
        verdict = self.policy.verdict(risk)

        if verdict == ALLOW:
            self.audit.record(risk, action, ALLOW, detail, outcome="permitted",
                              ts=self._ts())
            return

        if verdict == APPROVE:
            ok = self.approver.request(risk, action, detail)
            self.audit.record(risk, action, APPROVE, detail,
                              outcome="approved" if ok else "declined",
                              ts=self._ts())
            if not ok:
                raise PolicyDenied(f"operator declined {risk}:{action}")
            return

        # DENY (or unknown class -> fail closed)
        self.audit.record(risk, action, DENY, detail, outcome="blocked",
                          ts=self._ts())
        raise PolicyDenied(f"policy denies {risk}:{action}")

    def check_egress(self, url, action="model_call"):
        """Gate an outbound network call by destination host."""
        host = urlparse(url).hostname or url
        if self.policy.egress_allowed(host):
            self.audit.record("egress", action, ALLOW, {"host": host},
                              outcome="permitted", ts=self._ts())
            return
        self.audit.record("egress", action, DENY, {"host": host},
                          outcome="blocked", ts=self._ts())
        raise PolicyDenied(f"egress to {host!r} not on allowlist "
                           f"{self.policy.egress_allow}")


def govern(policy, approver=None, audit=None, clock=None):
    """Convenience constructor. `policy` may be a Policy or a posture name."""
    if not isinstance(policy, Policy):
        from .policy import load_policy
        policy = load_policy(policy)
    return Governor(policy, approver=approver, audit=audit, clock=clock)
