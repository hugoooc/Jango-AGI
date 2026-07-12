"""Desktop control layer for the CU loop on macOS.

Bridges Holo (perceives a screenshot, returns 0-1000 normalized coords) to
cliclick (acts in logical points). We screenshot the full display; because the
screenshot is Retina (2x) but the mouse works in logical points, we map Holo's
normalized 0-1000 output straight to logical points:

    point_x = norm_x / 1000 * logical_width
    point_y = norm_y / 1000 * logical_height

so the Retina factor cancels out and never has to be tracked explicitly.
"""
import os
import re
import time
import json
import base64
import subprocess
import urllib.request
import urllib.error

from PIL import Image

HERE = os.path.dirname(__file__)
SHOTS = os.path.join(HERE, "shots")
os.makedirs(SHOTS, exist_ok=True)

BASE = "https://api.hcompany.ai/v1"
MODEL = "holo3-1-35b-a3b"
_COORD_RE = re.compile(r'"x"\s*:\s*(-?\d+).*?"y"\s*:\s*(-?\d+)', re.S)


def _key():
    k = os.environ.get("HCOMPANY_API_KEY")
    if not k:
        env = os.path.join(HERE, "..", ".env")
        for line in open(env):
            if line.startswith("HCOMPANY_API_KEY="):
                k = line.split("=", 1)[1].strip()
    return k


def logical_size():
    """Logical screen size in points (what the mouse uses)."""
    import Quartz  # noqa
    # fall back to a subprocess python that has pyobjc if not here
    raise NotImplementedError


def _logical_size():
    out = subprocess.check_output([
        os.path.join(HERE, "..", ".venv", "bin", "python"), "-c",
        "import pyautogui,sys; s=pyautogui.size(); sys.stdout.write(f'{s.width} {s.height}')"
    ]).decode()
    w, h = out.split()
    return int(w), int(h)


LOGICAL_W, LOGICAL_H = _logical_size()


# ------------------------------------------------------------- screenshot

def screenshot(tag="shot"):
    """Capture the full display. Returns (path, PIL.Image)."""
    ts = int(time.time() * 1000)
    path = os.path.join(SHOTS, f"{tag}_{ts}.png")
    subprocess.run(["/usr/sbin/screencapture", "-x", "-o", path], check=True)
    return path, Image.open(path)


# ------------------------------------------------------------- Holo grounding

def _post(payload, timeout=25, retries=2, wall_clock=45):
    """POST to Holo with a HARD wall-clock ceiling. A slow endpoint must never
    hang a GUI-driving script (which would freeze the user's mouse). Total time
    across retries is bounded by `wall_clock` seconds; then it gives up cleanly."""
    last = None
    start = time.time()
    for attempt in range(retries):
        if time.time() - start > wall_clock:
            break
        try:
            req = urllib.request.Request(
                f"{BASE}/chat/completions", data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {_key()}",
                         "Content-Type": "application/json"}, method="POST")
            remaining = max(1, wall_clock - (time.time() - start))
            with urllib.request.urlopen(req, timeout=min(timeout, remaining)) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(min(1.5 * (attempt + 1), max(0, wall_clock - (time.time() - start))))
    raise RuntimeError(f"Holo unavailable within {wall_clock}s: {last}")


def _img_url(image, max_w=1512):
    """Encode as JPEG for Holo. Downscale wide screenshots — Holo normalizes to
    0-1000 so downscaling doesn't change returned coords, but it avoids the
    multi-MB payloads that make the endpoint time out."""
    if isinstance(image, str):
        image = Image.open(image)
    im = image.convert("RGB")
    if im.width > max_w:
        h = round(im.height * max_w / im.width)
        im = im.resize((max_w, h), Image.LANCZOS)
    import io
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}", im.size


def locate(image, description):
    """Return (point_x, point_y) in LOGICAL points for the described element."""
    url, (w, h) = _img_url(image)
    prompt = (
        "You are a precise UI localizer for a macOS application screenshot. "
        f"Find: {description}. "
        "Respond with ONLY JSON {\"x\": <int>, \"y\": <int>}, coordinates normalized "
        "0-1000 (x from left, y from top)."
    )
    resp = _post({"model": MODEL, "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": url}}]}],
        "temperature": 0.0, "max_tokens": 900})
    # Reasoning model: ~200-280 tokens of thought precede the JSON. 300 tokens
    # truncates it intermittently -> empty content. Give headroom + read the
    # `reasoning` field as a fallback so a long think never loses the coords.
    msg = resp["choices"][0]["message"]
    blob = (msg.get("content") or "") + "\n" + (msg.get("reasoning") or "")
    m = _COORD_RE.search(blob)
    if not m:
        raise RuntimeError(f"no coords: {blob!r}")
    nx, ny = int(m.group(1)), int(m.group(2))
    return round(nx / 1000 * LOGICAL_W), round(ny / 1000 * LOGICAL_H)


def ask(prompt, image=None, max_tokens=800):
    """VQA / verification. Returns text (content or reasoning)."""
    content = [{"type": "text", "text": prompt}]
    if image is not None:
        url, _ = _img_url(image)
        content.append({"type": "image_url", "image_url": {"url": url}})
    resp = _post({"model": MODEL, "messages": [{"role": "user", "content": content}],
                  "temperature": 0.0, "max_tokens": max_tokens}, timeout=90)
    msg = resp["choices"][0]["message"]
    return (msg.get("content") or msg.get("reasoning") or "").strip()


# ------------------------------------------------------------- input actions

def click(x, y, double=False):
    cmd = f"{'dc' if double else 'c'}:{x},{y}"
    subprocess.run(["cliclick", "-e", "50", cmd], check=True)
    time.sleep(0.3)


def move(x, y):
    subprocess.run(["cliclick", f"m:{x},{y}"], check=True)


def type_text(text):
    subprocess.run(["cliclick", "-e", "20", f"t:{text}"], check=True)
    time.sleep(0.2)


def key(keyname, modifiers=None):
    """keyname: cliclick key code (e.g. 'return','esc','tab'). modifiers: list."""
    if modifiers:
        for m in modifiers:
            subprocess.run(["cliclick", f"kd:{m}"], check=True)
    subprocess.run(["cliclick", f"kp:{keyname}"], check=True)
    if modifiers:
        for m in reversed(modifiers):
            subprocess.run(["cliclick", f"ku:{m}"], check=True)
    time.sleep(0.3)


def triple_click(x, y):
    """Triple-click selects all text in an FLTK field (so a following type
    replaces it). cliclick backspace APPENDS in these fields, so this is the
    only reliable clear-and-replace."""
    subprocess.run(["cliclick", f"tc:{x},{y}"], check=True)
    time.sleep(0.3)


def commit_return():
    """Commit an FLTK text field. cliclick's Return does NOT register the edit
    in OpenVSP fields; the AppleScript Return keystroke (key code 36) does.
    This was the fix that made parameter edits actually take."""
    subprocess.run(["osascript", "-e",
        'tell application "System Events" to key code 36'], check=True)
    time.sleep(0.8)


def click_menu(path):
    """Click a menu item by NAME PATH via AppleScript — position-independent,
    cannot drift to another app, works even if the menu is off-screen.
    path: ['Analysis', 'Aero', 'VSPAERO...'] (nested submenus supported)."""
    if len(path) == 1:
        raise ValueError("path needs at least [menu, item]")
    # Build nested "menu item X of menu Y of ..." expression.
    top = path[0]
    expr = f'menu item "{path[-1]}"'
    # walk from the deepest item outward through parent submenus
    parents = path[1:-1]  # submenu names between top menu and final item
    ref = expr
    # deepest submenu that contains the final item:
    if parents:
        # e.g. Analysis > Aero > VSPAERO...
        ref = f'menu item "{path[-1]}" of menu "{parents[-1]}"'
        for p in reversed(parents[:-1]):
            ref = f'{ref} of menu item "{p}" ...'  # (kept simple; 1 level typical)
        ref = f'{ref} of menu item "{parents[-1]}" of menu "{top}"' if len(parents) == 1 else ref
        # Simple, robust 1-submenu form (covers Analysis>Aero>VSPAERO):
        if len(parents) == 1:
            ref = (f'menu item "{path[-1]}" of menu "{parents[0]}" '
                   f'of menu item "{parents[0]}" of menu "{top}"')
    else:
        ref = f'menu item "{path[-1]}" of menu "{top}"'
    script = (f'tell application "System Events" to tell (first process whose '
              f'frontmost is true) to click {ref} of menu bar item "{top}" of menu bar 1')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"click_menu {path} failed: {r.stderr.strip()}")
    time.sleep(1.0)


def dump_menus():
    """Return OpenVSP's full menu tree {menu: [items...]} via AppleScript.
    This APIizes every menu-reachable command for free — no vision needed."""
    tree = {}
    top = frontmost_menus()  # ['Apple','vsp','File','Edit',...]
    for menu in top:
        if menu in ("Apple", "vsp", ""):
            continue
        r = subprocess.run(["osascript", "-e",
            f'tell application "System Events" to tell (first process whose frontmost '
            f'is true) to get name of menu items of menu "{menu}" of menu bar item '
            f'"{menu}" of menu bar 1'], capture_output=True, text=True)
        items = [i.strip() for i in r.stdout.split(",") if i.strip()]
        tree[menu] = items
    return tree


def frontmost_app():
    """Name of the currently frontmost GUI process."""
    out = subprocess.run(["osascript", "-e",
        'tell application "System Events" to get name of first process '
        'whose frontmost is true'], capture_output=True, text=True)
    return out.stdout.strip()


def focus_app(name_contains="vsp"):
    subprocess.run(["osascript", "-e",
        f'tell application "System Events" to tell process "{name_contains}" '
        f'to set frontmost to true'], check=False)
    time.sleep(0.6)


def menubar_app():
    """The app that OWNS THE MENU BAR — i.e. what is visually in front.
    This is the ground truth for 'what will my click hit', unlike the
    AppleScript `frontmost` process which can lie when windows overlap."""
    out = subprocess.run(["osascript", "-e",
        'tell application "System Events" to get name of first application '
        'process whose frontmost is true'], capture_output=True, text=True)
    return out.stdout.strip()


def frontmost_menus():
    """The menu-bar item names of the app that actually owns the menu bar.
    This is ground truth for 'what will my click hit' — deterministic, no Holo."""
    out = subprocess.run(["osascript", "-e",
        'tell application "System Events" to tell (first process whose frontmost '
        'is true) to get name of menu bar items of menu bar 1'],
        capture_output=True, text=True)
    return [m.strip() for m in out.stdout.split(",")]


def require_frontmost(name="vsp"):
    """HARD GUARD: confirm OpenVSP owns the menu bar by checking for its unique
    menus (Model + Analysis). Raise (NO clicks) if the wrong app is in front.
    Does NOT force-activate — OpenVSP (FLTK) resists programmatic raise, so the
    user must bring it forward; we only verify."""
    menus = frontmost_menus()
    has_vsp = "vsp" in [m.lower() for m in menus]
    has_signature = "Model" in menus and "Analysis" in menus
    if not (has_vsp and has_signature):
        raise RuntimeError(
            f"SAFETY ABORT: front app menus are {menus} — not OpenVSP. "
            f"No clicks issued. Click an OpenVSP window to bring it forward, then retry.")
    return menus


def cmd_key(letter, shift=False):
    """Send Cmd(+Shift)+letter via AppleScript keystroke — reliable for menus/dialogs."""
    using = '{command down' + (', shift down' if shift else '') + '}'
    subprocess.run(["osascript", "-e",
        f'tell application "System Events" to keystroke "{letter}" using {using}'],
        check=True)
    time.sleep(0.5)


if __name__ == "__main__":
    print(f"logical size: {LOGICAL_W}x{LOGICAL_H}")
    p, im = screenshot("selftest")
    print(f"screenshot {p} size(px)={im.size}")
