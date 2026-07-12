# Containerized OpenVSP GUI fleet

This prototype runs the real OpenVSP Linux GUI in isolated Docker containers.
It does not import or call the OpenVSP API. Each worker has its own X11 virtual
display, window manager, OpenVSP process, screenshot stream, mouse and keyboard,
HTTP control endpoint, and noVNC live viewer.

## Prerequisites

- Docker Desktop, OrbStack, or another local Docker engine
- Python 3.10+
- About 2 GB of free disk space for the first image build
- `HCOMPANY_API_KEY` or `HAI_API_KEY` only when running vision-grounded actions

The official OpenVSP Ubuntu package is AMD64. On Apple Silicon, Docker runs the
worker through AMD64 emulation. The first image build is therefore slower than
normal; subsequent builds and worker starts reuse Docker's cache.

## Start the demo

From the `sdk/` directory:

```bash
docker build --platform linux/amd64 \
  -f docker/Dockerfile.worker \
  -t legacypilot-openvsp-worker:dev .

cp .env.example .env
uv venv ../.venv --python 3.12
source ../.venv/bin/activate
uv pip install -r requirements-voice.txt
python -m orchestrator.server
```

Open <http://localhost:8765>, choose the number of workers, and click **Start
workers**. The dashboard creates the containers and displays a current screenshot
for each one. Click **Open live desktop** to watch or manually interact with the
real OpenVSP GUI through noVNC.

The **Voice** button captures the microphone on the host Mac and streams the
transcript through Gradium. Add `GRADIUM_API_KEY` to `sdk/.env`, speak a fleet
request, and press **Stop**; the completed transcript is submitted through the
same `/api/ask` route as typed requests. Microphone audio never enters an
OpenVSP worker container.

## Tests in the dashboard

### Visual smoke test (no API key required)

1. Start two workers.
2. Open both live desktops in separate browser tabs.
3. Click **Visual smoke test**.
4. Worker 1 should open the File menu and worker 2 should open the Edit menu.

This confirms that both OpenVSP processes render, accept mouse input, stream
live pixels, and remain isolated from one another.

### Vision-grounded parameter test

Set a key in `sdk/.env`:

```dotenv
HCOMPANY_API_KEY=your-key
```

Restart the orchestrator, enter comma-separated spans such as `10, 12`, and
click **Run vision task**. Holo locates the Wing row, Plan tab, and Span field
from screenshots. Each worker then uses only mouse and keyboard events to enter
its assigned value. Watch both live desktops and confirm the resulting geometry
is different.

## Stop and clean up

Use **Stop all** in the dashboard, or run:

```bash
docker rm -f $(docker ps -aq \
  --filter label=com.legacypilot.openvsp-worker=true)
```

Worker HTTP and noVNC ports default to `18080..18091` and `16080..16091` and
are bound to localhost only. The dashboard supports up to 12 workers by default;
actual parallel capacity depends on available CPU and memory.
