"""Characterization tests for the persistence / IO layer of ``app.py``.

Pins the CURRENT behavior of: read_json, write_json, slugify, topology_dir,
topology_path, compose_path, save_topology.

Every assertion is derived from reading the code (characterization), NOT from
an external spec. See TEST_PLAN.md "## File: test_store.py".
"""
import copy

import app
import pytest

from conftest import MINIMAL


# --- read_json / write_json -------------------------------------------------

def test_json_roundtrip(tmp_path):
    path = tmp_path / "x" / "payload.json"
    payload = {"b": 2, "a": [1, 2, 3], "nested": {"z": True, "y": None}}
    app.write_json(path, payload)
    assert app.read_json(path) == payload


def test_write_json_creates_parent_dirs(tmp_path):
    path = tmp_path / "deep" / "deeper" / "out.json"
    app.write_json(path, {"k": "v"})
    assert path.exists()
    # write_json dumps sort_keys=True with a trailing newline.
    assert path.read_text(encoding="utf8").endswith("\n")


def test_write_json_sorts_keys(tmp_path):
    path = tmp_path / "ordered.json"
    app.write_json(path, {"zeta": 1, "alpha": 2})
    text = path.read_text(encoding="utf8")
    # alpha must appear before zeta (sort_keys=True).
    assert text.index("alpha") < text.index("zeta")


def test_read_json_missing_file_raises():
    # read_json does NOT swallow errors; a missing file raises FileNotFoundError.
    with pytest.raises((FileNotFoundError, OSError)):
        app.read_json(app.TOPOLOGIES_DIR / "does-not-exist" / "topology.json")


def test_read_json_invalid_raises(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf8")
    import json as _json
    with pytest.raises(_json.JSONDecodeError):
        app.read_json(path)


# --- slugify ---------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("Test Lab", "test-lab"),
    ("UPPER Case!", "upper-case"),
    ("a---b", "a-b"),          # collapses runs and strips '-'
    ("   spaced  ", "spaced"),
])
def test_slugify(value, expected):
    assert app.slugify(value) == expected


def test_slugify_empty_falls_back_to_uuid_prefix():
    # slugify returns 'topology-<8 hex>' for empty/separator-only input.
    result = app.slugify("---")
    assert result.startswith("topology-")
    assert len(result) == len("topology-") + 8


# --- topology_dir / topology_path / compose_path ---------------------------

def test_topology_path_helpers():
    tid = "my-lab"
    assert app.topology_dir(tid) == app.TOPOLOGIES_DIR / tid
    assert app.topology_path(tid) == app.TOPOLOGIES_DIR / tid / "topology.json"
    assert app.compose_path(tid) == app.TOPOLOGIES_DIR / tid / "docker-compose.yml"


# --- save_topology ---------------------------------------------------------

def test_save_topology_returns_persisted_dict():
    payload = copy.deepcopy(MINIMAL)
    result = app.save_topology(payload)

    # Returns a dict carrying an id and timestamps.
    assert isinstance(result, dict)
    assert result.get("id"), "save_topology must assign an id"
    assert result.get("created_at")
    assert result.get("updated_at")
    # name preserved from MINIMAL.
    assert result["name"] == MINIMAL["name"]


def test_save_topology_writes_files_roundtrip():
    payload = copy.deepcopy(MINIMAL)
    result = app.save_topology(payload)
    tid = result["id"]

    topo_file = app.topology_path(tid)
    compose_file = app.compose_path(tid)
    assert topo_file.exists(), "topology.json must be written"
    assert compose_file.exists(), "docker-compose.yml must be written"

    # The persisted topology round-trips through read_json.
    persisted = app.read_json(topo_file)
    assert persisted["id"] == tid
    assert persisted["name"] == MINIMAL["name"]
    # validate_topology mutated the payload: hosts gained an 'agents' key.
    for network in persisted["networks"]:
        for host in network["hosts"]:
            assert "agents" in host
            assert host["image"] == "ubuntu:24.04"

    # The compose file is valid JSON (generate_compose emits a dict).
    import json as _json
    compose = _json.loads(compose_file.read_text(encoding="utf8"))
    assert isinstance(compose, dict)
    assert "services" in compose


def test_save_topology_preserves_created_at_on_overwrite():
    payload = copy.deepcopy(MINIMAL)
    first = app.save_topology(payload)
    tid = first["id"]
    original_created = first["created_at"]

    # Save again with the same id -> created_at preserved, updated_at refreshed.
    payload2 = copy.deepcopy(MINIMAL)
    payload2["id"] = tid
    second = app.save_topology(payload2)
    assert second["id"] == tid
    assert second["created_at"] == original_created
    assert second["updated_at"] is not None


def test_save_topology_assigns_random_suffix_when_no_id():
    # When the topology has no id and none can be derived, a 6-hex suffix is
    # appended to the slugified name.
    payload = copy.deepcopy(MINIMAL)
    payload.pop("id", None)
    # Force the no-id branch by removing name-derived slug determinism: give an
    # explicit empty id and rely on save_topology's uuid fallback.
    payload["id"] = ""
    result = app.save_topology(payload)
    # Base slug is slugify('Test Lab') == 'test-lab'; suffix is '-<6 hex>'.
    assert result["id"].startswith("test-lab-")
    suffix = result["id"][len("test-lab-"):]
    assert len(suffix) == 6
    int(suffix, 16)  # six hex chars
