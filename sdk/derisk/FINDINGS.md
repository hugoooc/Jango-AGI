# OpenVSP De-Risk — Findings

**Date:** 2026-07-11 · **Machine:** Apple Silicon (arm64), macOS 26.3, 11 CPU / 18 GB · **OpenVSP:** 3.51.0

**Verdict: GREEN. The full pipeline works end-to-end today. Build the demo.**

All four make-or-break risks are retired with working code (`derisk/slice.py`, `derisk/sweep.py`).

---

## What was proven

| Risk | Status | Evidence |
|------|--------|----------|
| 1. OpenVSP runs on this Mac | ✅ | Bundled 3.51.0 arm64 install present |
| 2. Python API imports headless | ✅ | `import openvsp` → `OpenVSP 3.51.0` in a 3.11 venv |
| 3. Full slice: build → set param → VSPAERO → read CL/CD | ✅ | `slice.py`: span-8 wing → CL=0.0737, CD=0.00182, **L/D=40.46** in 3.7s |
| 4. Parallel sweep (scale-out) | ✅ | `sweep.py`: 20 experiments, 6 workers, **15s total (0.75s/exp)**, ranked by L/D |

### Sample insight the pipeline already produces
- Best L/D: span=12, sweep=0° → **L/D=46.4**
- Most pitch-heavy: span=12, sweep=45° → **CMy=-0.529** (candidate "unsafe/unstable" story)

---

## Environment setup (reproducible)

```bash
# 1. Python 3.11 venv (the .so is built for py3.11 stable ABI; 3.13 dmg also available)
python3.11 -m venv .venv && source .venv/bin/activate

# 2. Install bundled API packages IN THIS ORDER (utilities is a hidden dep)
P=/path/to/OpenVSP-3.51.0-MacOS/python
pip install "$P/utilities" "$P/openvsp_config" "$P/degen_geom" "$P/openvsp"

# 3. import openvsp  ->  works headless
```

---

## Gotchas hit (and the fixes) — read before building

1. **Install location is a DMG mount / `~/Downloads`, not `/Applications`.** The app
   directory unmounts/moves mid-session. **Fix:** we vendored the solver binaries into
   `vendor/openvsp/` (43 MB, git-ignore or LFS). The Python `.so` is safe — pip copies it
   into the venv.

2. **Gatekeeper quarantines the unsigned `vspaero` binary**, making it intermittently
   `ENOENT` on execution. **Fix:** extract from the `.zip` (not the DMG) and
   `xattr -c vspaero`. Do this once when staging binaries.

3. **`pip install degen_geom` fails alone** — needs `utilities` installed first. Install in
   dependency order (see above).

4. **The results manager is unreliable across the sweep** — `GetStringResults(rid,"ResultsVec")`
   returned the CpSlice wrapper, not the polar, and results don't persist across processes.
   **Fix:** parse the `.polar` file directly (deterministic table: header row containing
   `Mach`/`CLtot`/`L/D`, then one data row per alpha). Robust and process-independent — this is
   what `read_polar()` does.

5. **OpenVSP global state is NOT thread-safe.** Parallelism must be process-based
   (`ProcessPoolExecutor`), one isolated workdir per experiment. Threads will corrupt state.

---

## Implications for the architecture

- **The 3-tier split is validated:** CU learns the GUI workflow → compiler emits an OpenVSP
  Python recipe (build geom + set parms + ExecAnalysis) → workers replay it in parallel
  processes. Scale-out is the Python API, not CU clicking 100×.
- **Throughput is a non-issue for the demo:** ~0.75s/experiment wall on 6 workers. 100
  experiments ≈ 75s on this laptop; trivially cloud-scalable later.
- **The "unsafe design" insight beat is real** — CMy/stability already falls out of the polar.
- **For an honest CU narrative:** the compiled recipe should be *derived from what CU observed
  in the GUI* (parm names, analysis panel), so the API is the discovered compilation target,
  not a hard-coded shortcut.

---

## Files

- `derisk/slice.py` — single end-to-end experiment (build → solve → read polar)
- `derisk/sweep.py` — 20-point parallel span×sweep study with L/D ranking + insight
- `vendor/openvsp/` — staged, de-quarantined solver binaries (`vspaero` etc.)
