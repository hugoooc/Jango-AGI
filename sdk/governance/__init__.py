"""NemoClaw-pattern governance for LegacyPilot's GUI-driving orchestrator.

Policy enforcement + operator approval + egress control + audit logging around
the actions the agent takes on OpenVSP. See governance/README.md for why this
is a *pattern* rather than the NemoClaw container product (the GUI driver must
run on the macOS host, not inside an OpenShell sandbox).
"""
from .policy import load_policy, Policy, PolicyDenied, ALLOW, APPROVE, DENY
from .governor import govern, Governor
from .approval import ConsoleApprover, AutoApprover, Approver
from .audit import AuditLog

__all__ = [
    "load_policy", "Policy", "PolicyDenied", "ALLOW", "APPROVE", "DENY",
    "govern", "Governor",
    "ConsoleApprover", "AutoApprover", "Approver", "AuditLog",
]
