# Jango SDK

The Jango SDK contains the task-native Chief Engineer runtime. Start with the
[architecture guide](JANGO.md) or the repository-level [README](../README.md).

## Components

- `introspection/recipe/` — active hosted Chief organization, task-local solver,
  OpenVSP mutation, live dashboard, skills, judges, and end-to-end tests.
- `chief_engineer/` and `docker/` — previous external-worker architecture,
  retained for historical/local comparison.
- `tests/` — tests for the earlier local orchestration kernel.

The old GUI/computer-use prototype remains in this repository as historical
reference. It is not part of Jango’s execution path.

## Run the active implementation

```bash
cd introspection/recipe
npm test
npm run smoke
```
