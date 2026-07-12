"""Skill registry — the learned "API" of the application.

A skill is a JSON file describing a reusable, parameterized procedure the agent
learned. The registry loads them and exposes them as a callable manifest, so
the agent can answer "what can I do with this software?" without re-discovering.

Skill JSON schema:
{
  "name": "set_wing_span",
  "summary": "Set the wing root section span.",
  "kind": "field" | "menu" | "composite",
  "params": [{"name": "value", "type": "float", "unit": "m"}],
  "preconditions": ["wing_editor_open", "sect_tab"],
  "steps": [ ... runtime step objects ... ],
  "verify": {"read_label": "Projected Span", "expect": "changes"},
  "outputs": [],
  "parm": {"geom": "Wing", "group": "XSec_1", "name": "Span"},   # backend hint
  "confidence": 0.0-1.0,
  "recovery": "re-localize then retry once",
  "learned_at": "<iso>",
  "verified": true|false
}

The `parm` block is the bridge to the fast backend: it records the underlying
parameter a GUI field maps to, discovered during learning. Both the GUI runtime
and the fast batch backend read the same skill, so the registry is a real
shared contract, not two disconnected code paths.
"""
import os
import json
import glob

HERE = os.path.dirname(__file__)
SKILLS_DIR = os.path.join(HERE, "skills")


class Skill(dict):
    @property
    def name(self):
        return self["name"]

    @property
    def params(self):
        return self.get("params", [])

    def param_names(self):
        return [p["name"] for p in self.params]

    def signature(self):
        args = ", ".join(f'{p["name"]}: {p.get("type","any")}' for p in self.params)
        return f'{self.name}({args})'


class Registry:
    def __init__(self, skills_dir=SKILLS_DIR):
        self.dir = skills_dir
        os.makedirs(self.dir, exist_ok=True)
        self.skills = {}
        self.load_all()

    def load_all(self):
        self.skills.clear()
        for path in glob.glob(os.path.join(self.dir, "*.json")):
            try:
                data = json.load(open(path))
                self.skills[data["name"]] = Skill(data)
            except Exception as e:
                print(f"[registry] skip {path}: {e}")
        return self

    def get(self, name):
        return self.skills.get(name)

    def save(self, skill):
        skill = Skill(skill)
        path = os.path.join(self.dir, f'{skill["name"]}.json')
        json.dump(skill, open(path, "w"), indent=2)
        self.skills[skill["name"]] = skill
        return path

    def list(self, verified_only=False):
        out = list(self.skills.values())
        if verified_only:
            out = [s for s in out if s.get("verified")]
        return sorted(out, key=lambda s: s["name"])

    def parameters(self):
        """All settable parameters exposed across field skills — these are the
        VARIABLES the analysis engine can sweep."""
        params = []
        for s in self.skills.values():
            if s.get("kind") == "field" and s.get("parm"):
                params.append({
                    "skill": s["name"],
                    "summary": s.get("summary", ""),
                    "parm": s["parm"],
                    "unit": (s["params"][0].get("unit") if s.get("params") else None),
                })
        return sorted(params, key=lambda p: p["skill"])

    def manifest(self):
        """Human/agent-readable 'API' of the software."""
        lines = ["# Learned API — available skills", ""]
        for s in self.list():
            v = "✓" if s.get("verified") else "?"
            lines.append(f"[{v}] {s.signature()} — {s.get('summary','')}")
        params = self.parameters()
        if params:
            lines += ["", "# Sweepable variables"]
            for p in params:
                lines.append(f"  - {p['skill']}  ({p['parm'].get('name')})")
        return "\n".join(lines)


if __name__ == "__main__":
    r = Registry()
    print(r.manifest() if r.skills else "no skills yet")
