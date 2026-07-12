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


def launch(model, timeout=90, settle=4.0):
    """Launch OpenVSP with `model` loaded. Returns once the process is up and a
    short settle has elapsed. `model` may be an absolute path or a models/ name.
    `settle`: seconds to wait after the process appears for the GUI to paint."""
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

    # IMPORTANT: gate on the PROCESS (pgrep, ~16ms), NOT AppleScript. The first
    # System Events query against a just-launched app blocks ~25-30s while macOS
    # registers the process with the accessibility subsystem — even though the
    # window is already on screen. Waiting on pgrep + a fixed settle is far
    # faster; the accessibility handshake then overlaps our own first action.
    t0 = time.time()
    while time.time() - t0 < timeout:
        if subprocess.run(["pgrep", "-x", "vsp"], capture_output=True).returncode == 0:
            time.sleep(settle)   # let the GUI paint + the model load into the tree
            return True
        time.sleep(0.2)
    raise RuntimeError(f"OpenVSP process did not start within {timeout}s")


def quit(app="vsp", timeout=8):
    """Kill OpenVSP fast. We never save (each session is disposable), so a hard
    pkill is correct and avoids the slow AppleScript 'is_running' handshake and
    any save-confirm dialog. Gated on pgrep (~16ms)."""
    if subprocess.run(["pgrep", "-x", app], capture_output=True).returncode != 0:
        return True
    subprocess.run(["pkill", "-x", app], capture_output=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if subprocess.run(["pgrep", "-x", app], capture_output=True).returncode != 0:
            time.sleep(0.3)   # let the port/window server release
            return True
        time.sleep(0.2)
    subprocess.run(["pkill", "-9", "-x", app], capture_output=True)
    time.sleep(0.5)
    return True
