"""Discovery — the agent learns the software, then compiles skills.

Three phases, cheapest first:

  1. map_menus()   AppleScript dumps the full menu tree. FREE, complete, instant.
                   Every menu command becomes a candidate skill.
  2. scan_panel()  Holo enumerates the controls visible in the current panel
                   (labels, likely value fields) -> UI map.
  3. learn_field() For a field with a known backend parm: set a test value via
                   the GUI, read it back, and if it changed -> save a VERIFIED
                   skill with its cached anchor. This is the self-teaching loop.

The knowledge is persisted (knowledge/menus.json, knowledge/ui_map.json,
skills/*.json) so a later session starts already knowing the software.
"""
import os
import json
import time

HERE = os.path.dirname(__file__)
KNOW = os.path.join(HERE, "knowledge")
os.makedirs(KNOW, exist_ok=True)


def map_menus(adapter):
    """Phase 1: dump every menu-reachable command. No vision."""
    adapter.guard()
    tree = adapter.menu_tree()
    json.dump(tree, open(os.path.join(KNOW, "menus.json"), "w"), indent=2)
    n = sum(len(v) for v in tree.values())
    print(f"[discover] mapped {n} menu commands across {len(tree)} menus")
    return tree


def scan_panel(adapter, panel_name, hint):
    """Phase 2: ask Holo to enumerate the controls in the current panel.
    Returns a list of {label, kind} and caches it."""
    _, im = adapter.screenshot("scan")
    prompt = (
        f"This is the '{panel_name}' panel of a desktop engineering app. "
        f"{hint} List the editable numeric parameter fields you can see, as a "
        f"JSON array of objects {{\"label\": <name>, \"kind\": \"number\"}}. "
        f"Only include real editable parameters, not buttons or tabs."
    )
    ans = adapter.ask(prompt, im, max_tokens=700)
    controls = _extract_json_array(ans)
    path = os.path.join(KNOW, f"panel_{panel_name}.json")
    json.dump({"panel": panel_name, "controls": controls}, open(path, "w"), indent=2)
    print(f"[discover] {panel_name}: found {len(controls)} candidate fields")
    return controls


def learn_field(adapter, registry, skill_name, label, parm, unit="",
                test_value=None, backend_check=True):
    """Phase 3: verify a GUI field controls `parm` by setting a test value and
    reading it back from the model. Saves a VERIFIED skill on success.

    Requires the field's editor panel to be open. Uses the backend to confirm
    the parameter actually changed (ground truth), not just the on-screen text.
    """
    key = f"wing.sect.{parm['name'].lower()}"
    desc = (f"the numeric value box on the far right of the "
            f"{parm['name'].replace('_',' ')} row")

    # baseline value from the model on disk (ground truth)
    before = _read_parm(parm) if backend_check else None
    tv = test_value if test_value is not None else (
        (before + 1.0) if before is not None else 5.0)

    adapter.set_field(key, desc, tv)          # GUI edit (triple-click/type/commit)
    adapter.run_save() if hasattr(adapter, "run_save") else None

    verified = True
    detail = "assumed"
    if backend_check:
        # persist via Cmd+S then read the file
        import desktop as d
        d.cmd_key("s"); time.sleep(1.5)
        after = _read_parm(parm)
        verified = after is not None and abs(after - tv) < 1e-3
        detail = f"set {tv}, read {after}"

    skill = {
        "name": skill_name,
        "summary": f"Set the wing {parm['name'].replace('_',' ').lower()}.",
        "kind": "field",
        "params": [{"name": "value", "type": "float", "unit": unit}],
        "preconditions": ["model_loaded", "wing_editor_open", "sect_tab"],
        "steps": [{"op": "set_field", "key": key, "desc": desc, "value": "{value}"}],
        "verify": {},
        "parm": parm,
        "confidence": 0.95 if verified else 0.3,
        "recovery": "re-localize field then retry once",
        "verified": verified,
        "learned_detail": detail,
    }
    if verified:
        registry.save(skill)
        print(f"[discover] LEARNED {skill_name}: {detail}  ✓")
    else:
        print(f"[discover] FAILED {skill_name}: {detail}  ✗ (not saved)")
    return skill


# ---- helpers -----------------------------------------------------------

def _read_parm(parm):
    """Ground-truth read of a parameter from the saved model file."""
    import openvsp as vsp
    M = os.path.abspath(os.path.join(HERE, "..", "models", "boeing777200.vsp3"))
    vsp.ClearVSPModel(); vsp.ReadVSPFile(M)
    for g in vsp.FindGeoms():
        if vsp.GetGeomName(g) == parm["geom"]:
            try:
                return vsp.GetParmVal(g, parm["name"], parm["group"])
            except Exception:
                return None
    return None


def _extract_json_array(text):
    import re
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except Exception:
        return []
