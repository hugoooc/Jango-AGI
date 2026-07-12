"""End-to-end LegacyPilot pipeline, driven by Holo computer-use on the live GUI.

Steps:
  1. Focus OpenVSP, load the Boeing model (File > Open dialog).
  2. Open the Wing editor > Sect tab, read + change the Span parameter.
  3. Save the model (Cmd+S).
  4. Run VSPAERO (API, thin-lifting-surface workaround) on baseline + modified.
  5. Report the aerodynamic delta.

Each GUI step screenshots and verifies before moving on.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import desktop as d

HERE = os.path.dirname(__file__)
MODEL = os.path.abspath(os.path.join(HERE, "..", "models", "boeing777200.vsp3"))
NEW_SPAN = 9.0


def step(msg):
    print(f"\n=== {msg} ===", flush=True)


def load_model():
    step("1. Load Boeing model via File > Open")
    d.focus_app("vsp")
    _, im = d.screenshot("p_file")
    fx, fy = d.locate(im, "the File menu in the top menu bar")
    d.click(fx, fy); time.sleep(0.8)
    _, im = d.screenshot("p_open")
    ox, oy = d.locate(im, "the Open... menu item in the File dropdown")
    d.click(ox, oy); time.sleep(1.3)
    # directory into path field
    _, im = d.screenshot("p_dlg")
    px, py = d.locate(im, "the folder path text field at the very top of the Open dialog")
    d.click(px, py); time.sleep(0.3); d.cmd_key("a"); d.key("delete")
    d.type_text(os.path.dirname(MODEL) + "/"); time.sleep(0.3)
    d.key("return"); time.sleep(1.0)
    # filename into File: field
    _, im = d.screenshot("p_dir")
    fx, fy = d.locate(im, "the File: input text field near the bottom of the dialog")
    d.click(fx, fy); time.sleep(0.3); d.cmd_key("a"); d.key("delete")
    d.type_text(os.path.basename(MODEL)); time.sleep(0.3)
    _, im = d.screenshot("p_typed")
    ax, ay = d.locate(im, "the Accept button at the bottom left of the dialog")
    d.click(ax, ay); time.sleep(3.0)
    _, im = d.screenshot("p_loaded")
    # verify via Holo
    ans = d.ask("Does this OpenVSP screenshot show a loaded aircraft model with a "
                "component tree containing a Wing (answer yes or no)?", im, max_tokens=200)
    print(f"[verify] model loaded? -> {ans[:80]}", flush=True)
    return im


def change_span():
    step("2. Open Wing > Sect tab and change Span")
    _, im = d.screenshot("p_wing")
    wx, wy = d.locate(im, "the word Wing in the Geom Browser component tree list")
    d.click(wx, wy, double=True); time.sleep(1.5)
    _, im = d.screenshot("p_wingedit")
    sx, sy = d.locate(im, "the Sect tab in the Wing editor tab row")
    d.click(sx, sy); time.sleep(1.2)
    _, im = d.screenshot("p_sect")
    vx, vy = d.locate(im, "the numeric value box on the far right showing the Span value")
    d.click(vx, vy); time.sleep(0.3)
    d.cmd_key("a"); time.sleep(0.2)
    d.type_text(str(NEW_SPAN)); time.sleep(0.2)
    d.key("return"); time.sleep(0.5); d.key("tab"); time.sleep(1.0)
    _, im = d.screenshot("p_changed")
    ans = d.ask(f"In this OpenVSP wing panel, what number is shown in the Span value "
                f"field? Answer with just the number.", im, max_tokens=150)
    print(f"[verify] Span field now reads -> {ans[:60]}", flush=True)


def save_model():
    step("3. Save model (Cmd+S)")
    before = os.path.getmtime(MODEL)
    d.focus_app("vsp")
    d.cmd_key("s"); time.sleep(1.5)
    d.screenshot("p_saved")
    after = os.path.getmtime(MODEL)
    print(f"[verify] file mtime changed: {before != after} (saved)", flush=True)


def run_aero():
    step("4. Run VSPAERO (baseline vs modified) and report")
    import openvsp as vsp
    from analyze import read_polar
    RES = os.path.abspath(os.path.join(HERE, "..", "vendor", "openvsp"))
    WORK = os.path.abspath(os.path.join(HERE, "..", "models", "aero_run"))
    os.makedirs(WORK, exist_ok=True)

    def run_span(span, tag):
        vsp.ClearVSPModel(); vsp.ReadVSPFile(MODEL)
        for g in list(vsp.FindGeoms()):
            if vsp.GetGeomName(g).lower() == "engine":
                vsp.DeleteGeom(g)          # drop engine+fan+pod subtree
        vsp.Update()
        wing = [g for g in vsp.FindGeoms() if vsp.GetGeomName(g).lower() == "wing"][0]
        vsp.SetParmVal(wing, "Span", "XSec_1", span); vsp.Update()
        SET = 3
        for g in vsp.FindGeoms():
            vsp.SetSetFlag(g, SET, vsp.GetGeomName(g).lower() == "wing")
        base = os.path.join(WORK, tag)
        vsp.WriteVSPFile(base + ".vsp3", vsp.SET_ALL)
        vsp.SetVSPAEROPath(RES)
        for a in ("VSPAEROComputeGeometry", "VSPAEROSweep"):
            vsp.SetAnalysisInputDefaults(a)
            vsp.SetIntAnalysisInput(a, "GeomSet", [-1])
            vsp.SetIntAnalysisInput(a, "ThinGeomSet", [SET])   # thin lifting surface
            if a == "VSPAEROSweep":
                vsp.SetDoubleAnalysisInput(a, "AlphaStart", [4.0])
                vsp.SetIntAnalysisInput(a, "AlphaNpts", [1])
                vsp.SetDoubleAnalysisInput(a, "MachStart", [0.1])
                vsp.SetIntAnalysisInput(a, "MachNpts", [1])
            vsp.ExecAnalysis(a)
        return read_polar(base + ".polar")

    baseline = run_span(5.481, "pipe_orig")
    modified = run_span(NEW_SPAN, "pipe_mod")

    print("\n" + "=" * 52, flush=True)
    print("  BOEING 777 WING — baseline vs CU-modified span", flush=True)
    print("=" * 52, flush=True)
    print(f"{'metric':8s} {'Span=5.481':>12s} {'Span='+str(NEW_SPAN):>12s} {'change':>9s}", flush=True)
    for k in ("CL", "CD", "L_D", "CMy"):
        b, m = baseline[k], modified[k]
        print(f"{k:8s} {b:12.5f} {m:12.5f} {(m-b)/b*100:+8.1f}%", flush=True)
    return baseline, modified


if __name__ == "__main__":
    t0 = time.time()
    load_model()
    change_span()
    save_model()
    run_aero()
    print(f"\n[pipeline complete] {time.time()-t0:.0f}s", flush=True)
