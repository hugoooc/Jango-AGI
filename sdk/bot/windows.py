"""Window geometry via AppleScript — deterministic, no vision.

macOS exposes every window's position and size through System Events. We use
that to make baked coordinates ROBUST: instead of storing an absolute screen
pixel (which breaks if the dialog opens somewhere else), we store an OFFSET
from the target window's top-left corner. At replay we re-read the live window
rect and reconstruct the absolute point.

The target window is matched by SIZE SIGNATURE (e.g. the VSPAERO dialog is the
~1000x828 one). OpenVSP's FLTK dialogs are unnamed, so name matching is out;
size is stable and unique enough to pick the right window.
"""
import subprocess

APP = "vsp"


def list_windows(app=APP):
    """Return [{'idx','name','x','y','w','h'}] for every window of `app`."""
    script = (
        f'tell application "System Events" to tell process "{app}"\n'
        'set out to ""\n'
        'set i to 0\n'
        'repeat with w in windows\n'
        '  set i to i + 1\n'
        '  set p to position of w\n'
        '  set s to size of w\n'
        '  set out to out & i & "\t" & (name of w) & "\t" & (item 1 of p) & "\t" '
        '& (item 2 of p) & "\t" & (item 1 of s) & "\t" & (item 2 of s) & linefeed\n'
        'end repeat\n'
        'return out\n'
        'end tell'
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    wins = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        idx, name, x, y, w, h = parts[:6]
        wins.append({"idx": int(idx), "name": name,
                     "x": int(x), "y": int(y), "w": int(w), "h": int(h)})
    return wins


def find_by_size(want_w, want_h, app=APP, tol=60):
    """Return the window whose size is closest to (want_w, want_h) within tol,
    or None. This is how we re-locate the VSPAERO dialog at replay time."""
    best, best_d = None, None
    for win in list_windows(app):
        d = abs(win["w"] - want_w) + abs(win["h"] - want_h)
        if d <= tol * 2 and (best_d is None or d < best_d):
            best, best_d = win, d
    return best


def close_by_size(want_w, want_h, app=APP):
    """Close the window matching a size signature (clicks its close button).
    Returns True if one was found and closed. Used to clear analysis dialogs
    (Mass Prop, VSPAERO) that would otherwise cover the Geom Browser tree."""
    import subprocess
    win = find_by_size(want_w, want_h, app)
    if not win:
        return False
    subprocess.run(["osascript", "-e",
        f'tell application "System Events" to tell process "{app}" to '
        f'click button 1 of window {win["idx"]}'], capture_output=True)
    return True


def raise_by_size(want_w, want_h, app=APP):
    """Bring the window matching a size signature to the FRONT (so a click into
    it can't land on an overlapping window). Returns the window dict or None.
    This is the window-discipline that keeps baked coordinates valid."""
    import subprocess
    win = find_by_size(want_w, want_h, app)
    if not win:
        return None
    subprocess.run(["osascript", "-e",
        f'tell application "System Events" to tell process "{app}" to '
        f'perform action "AXRaise" of window {win["idx"]}'], capture_output=True)
    return win


def to_offset(x, y, win):
    """Absolute point -> offset from a window's top-left corner."""
    return x - win["x"], y - win["y"]


def to_absolute(off_x, off_y, win):
    """Offset from a window's top-left corner -> absolute point."""
    return win["x"] + off_x, win["y"] + off_y
