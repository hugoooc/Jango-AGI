"""OpenVSP lifecycle — launch cold with a model, and quit cleanly.

The demo runs the whole thing on demand and closes OpenVSP at the end, so the
next run starts from a truly cold state. This module owns that:

  launch(model)  -> start `vsp <model.vsp3>`, wait until its window exists.
                    OpenVSP opens the app AND loads the model in one step
                    (vsp takes the .vsp3 as a CLI arg), so no File>Open needed.
  quit()         -> terminate the process (and dismiss the "save?" prompt).

Everything is deterministic (process + AppleScript window poll) — no vision.
"""
import os
import time
import subprocess

# GUI binary + resource root discovered on this machine.
VSP_BIN = os.path.expanduser("~/Downloads/OpenVSP-3.51.0-MacOS/vsp")
# vspaero solver must sit next to vsp OR be pointed at; we vendored a
# de-quarantined copy. VSP looks for it beside the binary, so we symlink/point.
VENDOR_AERO = os.path.join(os.path.dirname(__file__), "..", "vendor", "openvsp")


def _win_count(app="vsp"):
    r = subprocess.run(["osascript", "-e",
        f'tell application "System Events" to if exists process "{app}" then '
        f'return count of windows of process "{app}"'],
        capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def is_running(app="vsp"):
    r = subprocess.run(["osascript", "-e",
        f'tell application "System Events" to return (exists process "{app}")'],
        capture_output=True, text=True)
    return r.stdout.strip() == "true"


def launch(model, timeout=90):
    """Launch OpenVSP with `model` loaded. Returns when its windows appear.
    `model` may be an absolute path or a name under models/."""
    if not os.path.isabs(model):
        model = os.path.join(os.path.dirname(__file__), "..", "models", model)
    model = os.path.abspath(model)
    if not os.path.exists(model):
        raise FileNotFoundError(f"model not found: {model}")
    if not os.path.exists(VSP_BIN):
        raise FileNotFoundError(f"vsp binary not found: {VSP_BIN}")

    # Launch detached so it keeps running after this Python process returns.
    subprocess.Popen([VSP_BIN, model],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=os.path.dirname(VSP_BIN))
    # Poll for the app + its main windows (GL window + geom browser + titled window).
    t0 = time.time()
    while time.time() - t0 < timeout:
        if is_running() and _win_count() >= 2:
            time.sleep(2.0)   # let the model finish loading into the tree
            return True
        time.sleep(0.5)
    raise RuntimeError(f"OpenVSP did not open within {timeout}s")


def quit(app="vsp", timeout=8):
    """Quit OpenVSP and dismiss any 'save changes?' prompt (Don't Save).
    Repeatable-demo requirement: leave nothing running."""
    if not is_running(app):
        return True
    # Ask the process to quit; VSP may pop a save-confirm dialog.
    subprocess.run(["osascript", "-e",
        f'tell application "System Events" to tell process "{app}" to keystroke "q" using command down'],
        capture_output=True)
    time.sleep(1.2)
    # If a confirm dialog is up, press Escape / click a "Don't Save"-style button.
    subprocess.run(["osascript", "-e",
        'tell application "System Events" to key code 53'], capture_output=True)  # esc
    time.sleep(0.5)
    # Hard fallback: kill by name if still alive after grace period.
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not is_running(app):
            return True
        time.sleep(0.5)
    subprocess.run(["pkill", "-x", "vsp"], capture_output=True)
    time.sleep(1.0)
    return not is_running(app)
