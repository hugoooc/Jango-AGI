#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
SCREEN_WIDTH="${SCREEN_WIDTH:-1440}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-900}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
MODEL="${OPENVSP_MODEL:-/workspace/models/boeing777200.vsp3}"

pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT INT TERM

Xvfb "$DISPLAY" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" \
  -ac +extension GLX +render -noreset &
pids+=("$!")

for _ in $(seq 1 100); do
  xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
  sleep 0.1
done

openbox >/tmp/openbox.log 2>&1 &
pids+=("$!")

if [[ -f "$MODEL" ]]; then
  vsp "$MODEL" >/tmp/openvsp.log 2>&1 &
else
  vsp >/tmp/openvsp.log 2>&1 &
fi
pids+=("$!")

x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport "$VNC_PORT" \
  -quiet >/tmp/x11vnc.log 2>&1 &
pids+=("$!")

websockify --web=/usr/share/novnc/ "$NOVNC_PORT" "localhost:$VNC_PORT" \
  >/tmp/novnc.log 2>&1 &
pids+=("$!")

for _ in $(seq 1 180); do
  if pgrep -x vsp >/dev/null && xdotool search --onlyvisible --name OpenVSP >/dev/null 2>&1; then
    # A window can exist before FLTK has finished painting the model/tree. Do
    # not advertise a healthy worker until vision has stable pixels to inspect.
    sleep "${OPENVSP_SETTLE_SECONDS:-5}"
    break
  fi
  sleep 0.5
done

exec python3 -m container_worker.server
