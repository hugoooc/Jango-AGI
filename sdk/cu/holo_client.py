"""Client for H Company's Holo computer-use model (OpenAI-compatible).

Base: https://api.hcompany.ai/v1  ·  auth: Bearer $HCOMPANY_API_KEY
Model on the promo key: holo3-1-35b-a3b (35B / 3B active, vision+text).

Verified grounding convention (2026-07-11, this key/model):
  - Coordinates are NORMALIZED 0-1000 (Qwen-VL style), NOT absolute pixels.
    Multiply x/1000*width, y/1000*height to get pixels.
  - Do NOT send `chat_template_kwargs.enable_thinking=False` — it hangs the server.
  - Do NOT send `structured_outputs` nested in `extra_body` — hangs. A plain prompt
    asking for JSON works and returns in ~3-4s; parse the JSON out of the content.
  - The endpoint is intermittently slow / drops connections -> retry with backoff.
"""
import os
import io
import re
import time
import base64
import json
import urllib.request
import urllib.error

BASE = "https://api.hcompany.ai/v1"
DEFAULT_MODEL = "holo3-1-35b-a3b"
_COORD_RE = re.compile(r'"x"\s*:\s*(-?\d+).*?"y"\s*:\s*(-?\d+)', re.S)


def _key():
    k = os.environ.get("HCOMPANY_API_KEY")
    if not k:
        env = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env):
            for line in open(env):
                if line.startswith("HCOMPANY_API_KEY="):
                    k = line.split("=", 1)[1].strip()
    if not k:
        raise RuntimeError("HCOMPANY_API_KEY not set")
    return k


def _post(payload, timeout=45, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                f"{BASE}/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {_key()}",
                         "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Holo request failed after {retries} tries: {last}")


def _jpeg_data_url(image):
    from PIL import Image
    if isinstance(image, str):
        image = Image.open(image)
    im = image.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}", im.size


def localize(image, description, model=DEFAULT_MODEL, temperature=0.0):
    """Ask Holo where `description` is. Returns (x_px, y_px, (w, h))."""
    url, (w, h) = _jpeg_data_url(image)
    prompt = (
        "You are a precise UI localizer for a desktop application screenshot. "
        f"Find: {description}. "
        "Respond with ONLY a JSON object {\"x\": <int>, \"y\": <int>} giving the click "
        "location, where x and y are normalized to the range 0-1000 "
        "(x from the left edge, y from the top edge)."
    )
    resp = _post({
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": url}},
        ]}],
        "temperature": temperature,
        # This is a reasoning model: it emits ~200-280 tokens of chain-of-thought
        # BEFORE the JSON. With only 300 tokens the answer is intermittently
        # truncated (finish_reason=length, content='') -> "no coords". Headroom.
        "max_tokens": 900,
    })
    msg = resp["choices"][0]["message"]
    # Coords normally land in `content`; if the trace ran long they may only be
    # in `reasoning`. Search both so a long think never loses the answer.
    blob = (msg.get("content") or "") + "\n" + (msg.get("reasoning") or "")
    m = _COORD_RE.search(blob)
    if not m:
        raise RuntimeError(f"no coords in response: {blob!r}")
    nx, ny = int(m.group(1)), int(m.group(2))
    return int(nx / 1000 * w), int(ny / 1000 * h), (w, h)


def ask(prompt, image=None, model=DEFAULT_MODEL, max_tokens=1024, temperature=0.0):
    """General VQA / reasoning call. Returns text content (may use reasoning)."""
    content = [{"type": "text", "text": prompt}]
    if image is not None:
        url, _ = _jpeg_data_url(image)
        content.append({"type": "image_url", "image_url": {"url": url}})
    resp = _post({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }, timeout=90)
    msg = resp["choices"][0]["message"]
    return msg.get("content") or msg.get("reasoning") or ""


if __name__ == "__main__":
    import sys
    img = sys.argv[1] if len(sys.argv) > 1 else (
        os.path.join(os.path.dirname(__file__), "..",
                     "vendor/openvsp/vspaero_ex/Swept_Wing_API/Images/GUI_Sect.png"))
    desc = sys.argv[2] if len(sys.argv) > 2 else "the wing Span numeric value field (63.63)"
    x, y, size = localize(img, desc)
    print(f"localized '{desc}' -> ({x},{y}) px in {size}")
