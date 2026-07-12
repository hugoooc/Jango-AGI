"""governance CLI — NemoClaw-style single-command surface.

  python -m governance.cli status            posture + policy summary
  python -m governance.cli policy [name]     show a posture profile
  python -m governance.cli audit [--tail N]  recent audited actions
"""
import sys
import json

from .policy import load_policy, POSTURES
from .audit import AuditLog


def cmd_status():
    p = load_policy("standard")
    print("# LegacyPilot governance (NemoClaw-pattern)")
    print(f"  default posture : {p.name}")
    print(f"  egress allowlist: {p.egress_allow}")
    print("  risk-class verdicts:")
    for rc, v in p.rules.items():
        print(f"    {rc:12} -> {v}")
    print("\n  available postures:", ", ".join(POSTURES))


def cmd_policy(name="standard"):
    p = load_policy(name if name in POSTURES else name)
    print(json.dumps(p.summary(), indent=2))


def cmd_audit(tail=20):
    events = AuditLog().tail(tail)
    if not events:
        print("(no audit events yet)")
        return
    print(f"{'seq':>4} {'risk':12} {'action':16} {'verdict':8} outcome")
    for e in events:
        print(f"{e.get('seq',''):>4} {e.get('risk',''):12} "
              f"{e.get('action',''):16} {e.get('verdict',''):8} "
              f"{e.get('outcome','')}  {e.get('detail','')}")


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "status":
        cmd_status()
    elif cmd == "policy":
        cmd_policy(rest[0] if rest else "standard")
    elif cmd == "audit":
        n = 20
        if "--tail" in rest:
            n = int(rest[rest.index("--tail") + 1])
        cmd_audit(n)
    else:
        print(f"unknown command {cmd!r}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
