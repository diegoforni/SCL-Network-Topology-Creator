# network-topology plugin — monolith → flat modules

**Date:** 2026-07-25
**Status:** ✅ Complete & verified. Behavior-preserving (proven by characterization tests + golden masters + HTTP smoke + container build).

## What changed

`app.py` was a 3,608-line monolith (stdlib `http.server` app + a ~1,750-line embedded
frontend string). It is now **182 lines**: a config registry + re-export hub. All logic
lives in 12 flat sibling modules, and the frontend moved to `templates/index.html`.

```
app.py              182 lines  — config globals, .env loader, opencode_config import,
                                re-export hub (from <module> import *), entry point
helpers.py                     normalize_identifier, slugify, host_agents, shell_quote, now_ts
store.py                       topology_dir/path, compose_path, read_json, write_json
netmath.py                     subnet_prefix, router_ip, host_ip, hackerlab_ip, router_* maps
jobs.py                        start_job, get_job
llm.py                         generate_data_with_llm
scripts.py                     ssh/opencode_agent/host/router/hackerlab/role script emitters
images.py                      docker_command, build_opencode_image, ensure_*_image
compose.py                     resolve_run_id, generate_compose, *_network_name, project_name
docker_ops.py                  run_compose, start/stop_topology, is_running, sync_hackerlab_runtime
topology_model.py              validate_topology, summarize, list_topologies, save_topology
http_handlers.py               TopologyHandler (the 8 HTTP endpoints)
server.py                      handle_shutdown, main
templates/index.html           the UI (was the INDEX_HTML string blob)
Dockerfile                     COPY app.py → COPY *.py ./ (ships all modules)
tests/                         NEW regression suite (see below)
```

## How it stays behavior-preserving

Two design rules make the split provably safe:

1. **`app.X` indirection.** Every module does `import app` and references *all*
   cross-module symbols — config globals AND sibling functions — as `app.<name>`.
   So the test suite's `monkeypatch.setattr(app, 'OUTPUTS_HOST_PATH', …)` /
   `setattr(app, 'is_running', …)` still reach every call site, unchanged.
2. **`app.py` is the re-export hub.** `from <module> import *` pulls every public
   name back into `app`, so legacy `from app import X` and `app.X` access keep working.
   `app.py` also stays the `CMD ["python","-u","app.py"]` entry point — Dockerfile/compose
   run command is unchanged.

## The one real bug (found by the HTTP smoke test, invisible to the unit tests)

Running `python app.py` (the container CMD) loads the file as `__main__`. The sibling
modules' `import app` then created a **second** `app` module object, so `app.topology_path`
etc. missed the re-exports → `AttributeError: module 'app' has no attribute 'topology_path'`
on `POST /api/topologies`. The unit tests import `app` as a module (single object), so they
never hit it. **Fix:** two lines at the top of `app.py`:

```python
import sys as _sys
_sys.modules.setdefault('app', _sys.modules[__name__])
```

This aliases `app` → the running module for both `__main__` and `import app` contexts
(no-op under `import app`). This is exactly why the end-to-end HTTP smoke test was worth
running and not just the unit suite.

## Verification performed

| Check | Result |
|---|---|
| Characterization tests (88) on monolith — baseline | ✅ 88 passed (golden masters recorded) |
| Same 88 tests on refactored code | ✅ 88 passed (golden masters byte-identical) |
| `import app` (single-module context) | ✅ all re-exports present |
| HTTP smoke — `/health`, `/api/topologies`, `/` (UI), `POST /api/topologies`, `GET /<id>`, `/api/jobs/<id>` | ✅ all pass in `__main__` context |
| UI template move (`templates/index.html`) | ✅ renders, no `__HOST_TYPES__` leak |
| Plugin image `docker build` (Dockerfile `COPY *.py`) | ✅ builds clean, all modules present |
| Built image boot + `/health` + `/api/topologies` + `/` | ✅ healthy |
| pyflakes undefined-name scan across all modules | ✅ no undefined names |

Golden masters (`tests/golden/`) lock the exact output of `generate_compose` (minimal +
rich), `host_script`, `router_script`, `hackerlab_script`, `default_data_for_host` — any
behavior drift in a future edit fails the test.

## Not live-tested (and why that's OK)

`POST /api/topologies/<id>/start` and `/stop` were **not** exercised against a real docker
daemon — they need the full topology stack (image builds, hackerlab, networks) and the box
has known stack fragilities. Their code paths (`start_topology` → `ensure_opencode_images` →
`run_compose` → `sync_hackerlab_runtime`) were extracted **byte-verbatim** and the cross-module
call chain resolves correctly (proven by `save_topology`, which exercises the same multi-module
`app.X` resolution). The only thing unverified is docker *execution*, which the refactor does
not touch.

## Deploying to the live `scl-network-topology` container (left for you)

The running container (`Up`, healthy) still serves the **old** monolith image. To ship:

```bash
cd /home/diego/SCLT/stratocyberlab/plugins/network-topology
docker compose build network-topology     # rebuilds with COPY *.py + templates/index.html
docker compose up -d network-topology     # recreate (dashboard at :9005 talks to it on :9002)
```

The prod coder56 box (`:4096`) and `scl-hackerlab` are unrelated and must not be restarted.

## Follow-ups (optional, not done)

- `requirements.txt` still lists `quart`, `quart-cors`, `pydantic`, `docker`, `aiohttp`,
  `jinja2` — all unused by the app now (the dead `shared/` Pydantic package is the only pydantic
  user). Safe to drop once you're ready; left untouched to keep this refactor risk-free.
- `shared/`, `static/`, the placeholder `templates/index.html` history — the dead prior
  modularization attempt — still on disk (your call, per the plan: left for now).
- `images.py:85` has a pre-existing unused local `useradd_cmd` (copied verbatim); harmless.

## How to re-run the tests

```bash
cd /home/diego/SCLT/stratocyberlab/plugins/network-topology
python3 -m pytest tests/ -q          # 93 tests, ~0.9s; golden masters under tests/golden/
# to (re)capture a golden on purpose (e.g. after an intentional output change):
GOLDEN_RECORD=1 python3 -m pytest tests/ -q
```

## Post-refactor adversarial review (2026-07-25)

A dynamic review workflow (18 agents: 6 finders → per-finding adversarial verifiers → synthesis)
critiqued the change. **11 findings raised, 11 empirically confirmed, 0 false positives.** The core
refactor invariants (app.X indirection, shared mutable state, dual-import fix, no import-time app
access) are **clean** — an independent sweep found zero violations. Function bodies are byte-faithful
to the monolith modulo `app.` prefixing.

**Fixed in response:**
- **Doctype dropped** *(the one genuine regression I introduced)* — slicing `INDEX_HTML` into
  `templates/index.html` lost the leading `<!doctype html>` (it sat on the `r"""` assignment line).
  Served UI would have entered browser quirks mode. Fixed: prepended `<!doctype html>`; verified served.
- **Golden-master guard** — a deleted golden previously re-recorded silently, masking regressions. Now
  a missing golden FAILS unless `GOLDEN_RECORD=1` is set. Verified.
- **5 coverage tests** added (`tests/test_review_coverage.py`) pinning paths the suite had only
  exercised implicitly: multi-router/transit compose (new golden), router normalization (dup-id +
  bad-parent), firewall allowed-pair filter, `is_running` real branch via `run_compose`, and the
  `ssh lab` topology+compose deletion. Suite is now **93 tests**.

**Remaining (not blocking deploy):**
- **`app.defaultRouters()` undefined** (`compose.py:48`) — **pre-existing** in the monolith (line 2618),
  carried over verbatim; dead in production (`validate_topology` always synthesizes ≥1 router). Latent
  `AttributeError` only if `generate_compose` is called on an un-validated dict with no routers. Worth a
  follow-up ticket (define it or delete the dead `if not routers:` branch), but NOT introduced by this refactor.
- 4 modules drop a trailing blank line at EOF vs. the monolith — zero runtime effect.

**Verdict:** safe to deploy (after the doctype fix, now done).
