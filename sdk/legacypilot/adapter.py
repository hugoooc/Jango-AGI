"""LegacyPilot adapter: the reliable driving layer over a GUI application.

Wraps the low-level desktop controls (cu/desktop.py) with the reliability
lessons learned the hard way on OpenVSP:

  - focus guard before every action (never click into the wrong app)
  - menu commands via AppleScript (position-independent, no vision)
  - FLTK field edits: triple-click to clear, type, AppleScript-Return to commit
  - anchor cache: a named UI element is located by Holo ONCE, then reused;
    vision only re-fires on a cache miss or a failed post-action verify
  - read outputs from the files the app exports (not its scripting API)

The point: after discovery, running a task needs almost no vision — most steps
are menu clicks (free) or cached-coordinate field edits.
"""
import os
import sys
import json
import time

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "cu"))
import desktop as d  # noqa: E402

ANCHORS_PATH = os.path.join(HERE, "knowledge", "anchors.json")


class Adapter:
    def __init__(self, app="vsp", anchors_path=ANCHORS_PATH):
        self.app = app
        self.anchors_path = anchors_path
        self.anchors = self._load_anchors()

    # ---- safety ---------------------------------------------------------
    def guard(self, raise_first=True):
        """Ensure the target app owns the menu bar, then verify. Raises the app
        via System Events (works on OpenVSP's FLTK windows) before checking, so
        a script that runs after Terminal took focus still lands its clicks on
        the right app. Aborts with NO clicks if the app can't be brought front."""
        if raise_first:
            import subprocess
            subprocess.run(["osascript", "-e",
                f'tell application "System Events" to set frontmost of process '
                f'"{self.app}" to true'], capture_output=True)
            import time
            time.sleep(0.8)
        return d.require_frontmost(self.app)

    # ---- anchors (learned coordinates) ----------------------------------
    def _load_anchors(self):
        if os.path.exists(self.anchors_path):
            return json.load(open(self.anchors_path))
        return {}

    def _save_anchors(self):
        os.makedirs(os.path.dirname(self.anchors_path), exist_ok=True)
        json.dump(self.anchors, open(self.anchors_path, "w"), indent=2)

    def locate(self, key, description, force=False):
        """Return (x, y) for a named UI element. On a fixed screen these
        positions are STABLE (OpenVSP opens its windows/dialogs at the same
        pixels every time), so we cache the coordinate once and reuse it —
        no vision, no hang on replay. Vision fires only on first sight or
        `force` (used by the verify-and-relearn recovery path)."""
        if not force and key in self.anchors:
            a = self.anchors[key]
            return a["x"], a["y"]
        _, im = d.screenshot("locate")
        x, y = d.locate(im, description)
        self.anchors[key] = {"x": x, "y": y, "desc": description}
        self._save_anchors()
        return x, y

    def forget(self, key):
        self.anchors.pop(key, None)
        self._save_anchors()

    # ---- primitive actions ---------------------------------------------
    def menu(self, path):
        """Menu command by name path, e.g. ['File', 'Open...']."""
        self.guard()
        d.click_menu(path)

    def click(self, key, description, double=False, no_guard=False):
        # no_guard: skip re-raising the app. Use for clicks INSIDE a modal dialog,
        # where re-raising the process would steal focus from the dialog.
        if not no_guard:
            self.guard()
        x, y = self.locate(key, description)
        d.click(x, y, double=double)

    def set_field(self, key, description, value):
        """Robust FLTK numeric/text field edit: focus, select-all, type, commit."""
        self.guard()
        x, y = self.locate(key, description)
        d.triple_click(x, y)          # selects existing text
        d.type_text(str(value))
        d.commit_return()             # AppleScript Return — the edit that sticks

    def fill_field(self, key, description, value):
        """Fill a text field WITHOUT committing (Return). For dialog fields where
        Return would fire the default button (e.g. Open dialog path/file)."""
        self.guard()
        x, y = self.locate(key, description)
        d.triple_click(x, y)
        d.type_text(str(value))

    def key(self, name):
        self.guard()
        d.key(name)

    def cmd(self, letter, shift=False):
        self.guard()
        d.cmd_key(letter, shift=shift)

    def screenshot(self, tag="shot"):
        return d.screenshot(tag)

    # ---- perception / verification -------------------------------------
    def ask(self, prompt, image=None, max_tokens=400):
        if image is None:
            _, image = d.screenshot("ask")
        return d.ask(prompt, image, max_tokens=max_tokens)

    def read_number(self, label):
        """Vision-read a numeric readout by its label (used for verify)."""
        _, im = d.screenshot("read")
        ans = d.ask(f"In this screenshot, what number is shown for '{label}'? "
                    f"Answer with only the number.", im, max_tokens=80)
        return ans.strip()

    # ---- knowledge dumps (free, no vision) -----------------------------
    def menu_tree(self):
        self.guard()
        return d.dump_menus()


# module-level singleton for convenience
_default = None


def default():
    global _default
    if _default is None:
        _default = Adapter()
    return _default
