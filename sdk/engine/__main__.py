"""Run a design question from the shell.

  # one-shot:
  python -m engine "impact on mass if wingspan goes to 12m?"

  # interactive REPL (ask many):
  python -m engine

Prerequisite: OpenVSP must already be running with the model loaded
(python -c "import sys;sys.path.insert(0,'bot');import lifecycle as L;L.launch('boeing777200.vsp3')").
Add --slow to allow the minutes-long VSPAERO (L/D) outputs.
"""
import json
import sys

from . import ask


def _run(question, fast_only):
    print(f"\n> {question}")
    try:
        report = ask(question, fast_only=fast_only)
    except Exception as e:
        print(f"  error: {type(e).__name__}: {e}")
        return
    print(json.dumps(report, indent=2, default=str))


def main(argv):
    fast_only = "--slow" not in argv
    argv = [a for a in argv if a != "--slow"]
    if argv:                                  # one-shot
        _run(" ".join(argv), fast_only)
        return
    print("engine REPL — type a design question (empty line or Ctrl-D to quit)")
    while True:
        try:
            q = input("\nask> ").strip()
        except EOFError:
            break
        if not q:
            break
        _run(q, fast_only)


if __name__ == "__main__":
    main(sys.argv[1:])
