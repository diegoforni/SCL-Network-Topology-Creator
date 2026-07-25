"""Coverage tests added in response to the adversarial review (2026-07-25).
These pin behavior paths the original 88-test suite exercised only implicitly
or not at all. They assert CURRENT behavior (characterization)."""
import app
import docker_ops  # is_running calls the SAME-MODULE run_compose, so patch it here


# --- #3 multi-router / transit-network compose path (was entirely untested) ---
def test_compose_multi_router_transit(make_topology, golden):
    t = make_topology(
        {
            "name": "Two Router",
            "networks": [
                {"id": "dmz", "cidr": "10.77.10.0/24", "internet": True,
                 "hosts": [{"id": "w", "type": "web-server"}]},
                {"id": "int", "cidr": "10.77.20.0/24", "internet": False,
                 "hosts": [{"id": "b", "type": "jump-box"}]},
            ],
            "routers": [{"id": "r1", "name": "core"},
                        {"id": "r2", "name": "edge", "parent_router_id": "r1"}],
        },
        topo_id="two-router",
    )
    compose = app.generate_compose(t)
    golden("compose_two_router.json", compose)  # full golden master
    services = list(compose["services"].keys())
    networks = list(compose["networks"].keys())
    # one router service per router
    assert "router-r1" in services and "router-r2" in services
    # parent->child transit link is emitted as its own network
    assert "transit_r1_r2" in networks


# --- #4 validate_topology router normalization: dup-id suffix + bad-parent reparent ---
def test_validate_router_normalization(make_topology):
    t = make_topology(
        {
            "name": "Norm",
            "networks": [{"id": "n1", "hosts": [{"id": "h1"}]}],
            "routers": [{"id": "r1"}, {"id": "r1"},
                        {"id": "r3", "parent_router_id": "NOPE"}],
        },
        topo_id="norm",
    )
    result = [(r["id"], r["parent_router_id"]) for r in t["routers"]]
    # duplicate id 'r1' -> second gets '-2' suffix + reparented to root;
    # 'r3' with a nonexistent parent -> reparented to the root 'r1'.
    assert result == [("r1", ""), ("r1-2", "r1"), ("r3", "r1")]


# --- #5 firewall allowed-pair filter (keeps only 'src->dst' strings) ---
def test_validate_firewall_allowed_filter(make_topology):
    t = make_topology(
        {
            "name": "Fw",
            "networks": [{"id": "n1", "hosts": [{"id": "h1"}]}],
            "router": {"firewall": {"allowed": ["a->b", "invalid", "c->d", 5, None]}},
        },
        topo_id="fw",
    )
    assert t["router"]["firewall"]["allowed"] == ["a->b", "c->d"]


# --- #6 is_running exercises the real (compose-file-present) branch via run_compose ---
def test_is_running_uses_run_compose_branch(monkeypatch, make_topology):
    t = make_topology(
        {"name": "RunStat", "networks": [{"id": "n1", "hosts": [{"id": "h1"}]}]},
        topo_id="runstat",
    )
    # Write a compose file so is_running does NOT short-circuit on "file absent".
    app.write_json(app.compose_path("runstat"), app.generate_compose(t))
    assert app.compose_path("runstat").exists()

    def _running(_tid, _args):
        return "router-r1\nweb\n"

    def _empty(_tid, _args):
        return ""

    def _boom(_tid, _args):
        raise RuntimeError("docker down")

    # non-empty stdout -> True, and it must actually invoke compose 'ps'
    seen = {}

    def _spy(tid, args):
        seen["args"] = args
        return "svc\n"

    monkeypatch.setattr(docker_ops, "run_compose", _spy)
    assert app.is_running("runstat") is True
    assert seen["args"][0] == "ps"

    monkeypatch.setattr(docker_ops, "run_compose", _empty)
    assert app.is_running("runstat") is False

    # a failing run_compose is swallowed -> False (not raised)
    monkeypatch.setattr(docker_ops, "run_compose", _boom)
    assert app.is_running("runstat") is False


# --- #8 list_topologies deletes 'ssh lab' topology.json AND its compose file ---
def test_list_topologies_deletes_ssh_lab_and_compose(monkeypatch):
    # save_topology calls is_running cross-module (app.is_running) -> patch to avoid docker.
    monkeypatch.setattr(app, "is_running", lambda *a, **k: False)
    saved = app.save_topology(
        {"name": "ssh lab", "networks": [{"id": "n1", "hosts": [{"id": "h1"}]}]}
    )
    tid = saved["id"]
    assert app.topology_path(tid).exists()
    assert app.compose_path(tid).exists()  # save_topology writes the compose file too

    result = app.list_topologies()  # should delete the 'ssh lab' entry + both files
    assert result == []
    assert not app.topology_path(tid).exists()
    assert not app.compose_path(tid).exists()
