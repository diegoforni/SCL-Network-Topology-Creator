"""Shared pytest fixtures for the network-topology plugin characterization tests.

These tests pin the CURRENT behavior of the monolithic ``app.py`` so the
flat-module refactor can prove it is behavior-preserving. Every assertion is
derived from reading the code (characterization), NOT from an external spec.

Run from the plugin root:  python3 -m pytest tests/
"""
import copy
import json
import sys
from pathlib import Path

# Make ``import app`` work regardless of cwd.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402  (import once at collection time)

# Canonical minimal topology (no explicit routers/firewall/monitoring —
# validate_topology fills them in). Used as the base for most fixtures.
MINIMAL = {
    "name": "Test Lab",
    "networks": [
        {
            "id": "net1",
            "name": "Net One",
            "cidr": "10.77.1.0/24",
            "internet": True,
            "hosts": [
                {"id": "h1", "name": "host1", "type": "web-server"},
                {"id": "h2", "name": "host2", "type": "normal-user"},
            ],
        }
    ],
}

# Richer topology: two networks + an agent-enabled host (exercises router maps,
# the opencode agent block and the full compose output).
RICH = {
    "name": "Rich Lab",
    "networks": [
        {
            "id": "dmz",
            "name": "DMZ",
            "cidr": "10.77.10.0/24",
            "internet": True,
            "hosts": [
                {"id": "web", "name": "web", "type": "web-server"},
            ],
        },
        {
            "id": "int",
            "name": "Internal",
            "cidr": "10.77.20.0/24",
            "internet": False,
            "hosts": [
                {"id": "box", "name": "jumpbox", "type": "jump-box",
                 "agent_enabled": True, "agent_type": "coder56"},
            ],
        },
    ],
}


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_fs(tmp_path, monkeypatch):
    """Point OUTPUTS / DATA / TOPOLOGIES dirs at per-test tmp dirs so tests never
    touch the real /app/data or /tmp/outputs, and pin GUARDED_AGENTS order
    (opencode_config load order is non-deterministic across processes)."""
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    data = tmp_path / "data"
    topo = data / "topologies"
    topo.mkdir(parents=True)
    monkeypatch.setattr(app, "OUTPUTS_HOST_PATH", str(outputs))
    monkeypatch.setattr(app, "DATA_DIR", data)
    monkeypatch.setattr(app, "TOPOLOGIES_DIR", topo)
    monkeypatch.setattr(app, "GUARDED_AGENTS", tuple(sorted(app.GUARDED_AGENTS)))
    yield


@pytest.fixture
def make_topology():
    """Factory: deep-copy a spec dict, run validate_topology, assign a
    deterministic top-level id (avoids save_topology's random uuid suffix),
    and return the normalized topology."""

    def _make(spec, topo_id=None):
        t = copy.deepcopy(spec)
        app.validate_topology(t)
        t["id"] = topo_id or app.slugify(t["name"])
        return t

    return _make


@pytest.fixture
def minimal_topology(make_topology):
    return make_topology(MINIMAL)


@pytest.fixture
def rich_topology(make_topology):
    return make_topology(RICH)


@pytest.fixture
def golden():
    """Golden-master helper. A golden that is PRESENT is asserted byte-exact.
    A golden that is ABSENT is only recorded when GOLDEN_RECORD=1 is set
    (the baseline-capture mode); otherwise it FAILS — so a deleted golden plus a
    behavior drift can never silently re-record itself as the new truth. To
    (re)capture a golden on purpose: run ``GOLDEN_RECORD=1 python3 -m pytest``."""
    import os
    gdir = Path(__file__).parent / "golden"
    gdir.mkdir(exist_ok=True)
    record_mode = bool(os.environ.get("GOLDEN_RECORD"))

    def _check(name, actual):
        text = actual if isinstance(actual, str) else json.dumps(
            actual, sort_keys=True, indent=2)
        path = gdir / name
        if not path.exists():
            assert record_mode, (
                f"golden {name} is missing and GOLDEN_RECORD is not set — refusing "
                f"to silently record. If this is intentional (new golden), re-run "
                f"with GOLDEN_RECORD=1.")
            path.write_text(text)
            return  # recorded
        assert path.read_text() == text, f"golden mismatch: {name}\n--- expected ---\n{path.read_text()}\n--- actual ---\n{text}"

    return _check
