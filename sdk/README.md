# Jango SDK

The Jango SDK contains the API-first Chief Engineer runtime. Start with the
[architecture guide](JANGO.md) or the repository-level [README](../README.md).

## Components

- `chief_engineer/` — capability discovery, planning, specialist orchestration,
  worker provisioning, solver adapters, mission persistence, MCP, and control room.
- `docker/` — isolated OpenVSP API worker image.
- `introspection/recipe/` — hosted Chief organization, skills, and judges.
- `tests/` — domain-neutral orchestration and improvement-policy tests.

The old GUI/computer-use prototype remains in this repository as historical
reference. It is not part of Jango’s execution path.

## Run

```bash
MODEL_PATH="$PWD/models/boeing777200.vsp3" \
CHIEF_WORKER_PROVIDER=docker \
CHIEF_WORKER_IMAGE=hacknation-openvsp-api-worker:3.51 \
CHIEF_ENGINEER_WORKDIR="$PWD/chief-engineer-runs" \
../.venv/bin/python -m chief_engineer.server
```

Open `http://127.0.0.1:8765` for the live Jango control room.
