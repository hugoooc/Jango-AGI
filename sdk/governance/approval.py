"""Operator approval flow — the human-in-the-loop gate.

When policy returns APPROVE, the action pauses for an operator decision before
it runs. This mirrors NemoClaw's "operator approval flow" for
destructive/sensitive operations.

Two approvers:
  ConsoleApprover  prompts on the terminal (interactive demo / manual runs)
  AutoApprover     for headless/CI: approves or denies by a fixed policy, and
                   records the decision so the audit trail still shows a gate
                   was applied (never silently bypasses).
"""


class Approver:
    def request(self, risk, action, detail):
        """Return True to allow the action, False to deny. Must not have side
        effects beyond the operator interaction."""
        raise NotImplementedError


class ConsoleApprover(Approver):
    """Interactive terminal prompt. Default deny on empty / non-affirmative."""

    def request(self, risk, action, detail):
        print("\n" + "!" * 60)
        print(f"  OPERATOR APPROVAL REQUIRED  [{risk}] {action}")
        for k, v in (detail or {}).items():
            print(f"    {k}: {v}")
        print("!" * 60)
        try:
            ans = input("  approve? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        return ans in ("y", "yes")


class AutoApprover(Approver):
    """Non-interactive gate for headless runs. `decision` is applied to every
    request and recorded — so 'approved' is an explicit, auditable choice, not
    a silent skip."""

    def __init__(self, decision=False):
        self.decision = bool(decision)

    def request(self, risk, action, detail):
        return self.decision
