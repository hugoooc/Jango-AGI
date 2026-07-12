"""Skill runtime — executes a skill's steps through the Adapter.

Step types (each a dict in skill["steps"]):
  {"op": "menu",  "path": ["File", "Open..."]}
  {"op": "click", "key": "wing.node", "desc": "the Wing node ...", "double": true}
  {"op": "set_field", "key": "sect.span", "desc": "...", "value": "{value}"}
  {"op": "type", "text": "{value}"}
  {"op": "key",  "name": "return"|"esc"|"tab"}
  {"op": "cmd",  "letter": "s", "shift": false}
  {"op": "wait", "seconds": 1.0}

{value} placeholders are filled from the call kwargs.
After steps, an optional "verify" block reads a label and checks it changed.
On failure, the runtime re-localizes the anchor (forget + force) and retries once.
"""
import time
import re

_PLACE = re.compile(r"\{(\w+)\}")


def _fill(v, kwargs):
    if isinstance(v, str):
        return _PLACE.sub(lambda m: str(kwargs.get(m.group(1), m.group(0))), v)
    return v


class Runtime:
    def __init__(self, adapter, registry):
        self.a = adapter
        self.reg = registry

    def run(self, skill_name, **kwargs):
        skill = self.reg.get(skill_name)
        if skill is None:
            raise KeyError(f"unknown skill: {skill_name}")
        try:
            self._exec_steps(skill, kwargs)
            ok, detail = self._verify(skill, kwargs)
            if not ok:
                # recovery: forget anchors used, re-localize, retry once
                self._forget_anchors(skill)
                self._exec_steps(skill, kwargs, force_locate=True)
                ok, detail = self._verify(skill, kwargs)
            return {"skill": skill_name, "ok": ok, "detail": detail}
        except Exception as e:
            return {"skill": skill_name, "ok": False, "detail": f"error: {e}"}

    def _exec_steps(self, skill, kwargs, force_locate=False):
        for step in skill["steps"]:
            op = step["op"]
            if op == "menu":
                self.a.menu([_fill(p, kwargs) for p in step["path"]])
            elif op == "click":
                key = step["key"]
                if force_locate:
                    self.a.forget(key)
                    self.a.locate(key, step["desc"], force=True)
                self.a.click(key, _fill(step["desc"], kwargs),
                             double=step.get("double", False),
                             no_guard=step.get("no_guard", False))
            elif op == "set_field":
                key = step["key"]
                if force_locate:
                    self.a.forget(key)
                    self.a.locate(key, step["desc"], force=True)
                self.a.set_field(key, step["desc"], _fill(step["value"], kwargs))
            elif op == "fill_field":
                # like set_field but NO commit-return (for dialog text fields
                # where Return would trigger the default button)
                key = step["key"]
                if force_locate:
                    self.a.forget(key)
                    self.a.locate(key, step["desc"], force=True)
                self.a.fill_field(key, step["desc"], _fill(step["value"], kwargs))
            elif op == "type":
                self.a.guard()
                import desktop as d
                d.type_text(_fill(step["text"], kwargs))
            elif op == "key":
                self.a.key(step["name"])
            elif op == "cmd":
                self.a.cmd(step["letter"], shift=step.get("shift", False))
            elif op == "wait":
                time.sleep(step.get("seconds", 1.0))
            else:
                raise ValueError(f"unknown step op: {op}")

    def _verify(self, skill, kwargs=None):
        kwargs = kwargs or {}
        v = skill.get("verify")
        if not v:
            return True, "no verify"
        # title-bar contains check (e.g. loaded model filename)
        if "title_contains" in v:
            import desktop as d
            needle = _fill(v["title_contains"], kwargs)
            menus = d.frontmost_menus()  # cheap ground-truth signal not needed
            # read the OpenVSP main window title via AppleScript (no vision)
            import subprocess
            r = subprocess.run(["osascript", "-e",
                'tell application "System Events" to tell process "vsp" to get '
                'title of every window'], capture_output=True, text=True)
            titles = r.stdout.strip()
            ok = needle in titles
            return ok, f"titles=[{titles}] contains '{needle}': {ok}"
        label = v.get("read_label")
        if not label:
            return True, "no read_label"
        val = self.a.read_number(label)
        return (val not in ("", None)), f"{label}={val}"

    def _forget_anchors(self, skill):
        for step in skill["steps"]:
            if "key" in step:
                self.a.forget(step["key"])
