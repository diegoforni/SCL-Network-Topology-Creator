# Characterization Test Plan — network-topology plugin

Goal: **pin the current behavior of `app.py`** so the flat-module refactor is provably behavior-preserving. Every test is a *characterization* test — assertions match what the code does today, derived by reading `app.py`. Do NOT invent a spec.

All tests `import app` and use fixtures from `tests/conftest.py`:
- `make_topology(spec, topo_id=None)` → validated, deterministic-id topology
- `minimal_topology` / `rich_topology` → ready-made validated topologies
- `golden` → `golden(name, actual)` records on first run, asserts byte-exact on later runs
- `isolate_fs` (autouse) → OUTPUTS/DATA/TOPOLOGIES dirs are tmp; `app.GUARDED_AGENTS` pinned sorted

## File: test_netmath.py — pure IP/topology-graph helpers
All deterministic, no fixtures needed beyond import.
- `app.subnet_prefix('10.77.1.0/24')` == `'10.77.1'`
- `app.subnet_prefix('192.168.0.5/16')` == `'192.168.0'` (rsplit on last '.', drops host octet+prefix)
- `app.router_ip('10.77.1.0/24')` == `'10.77.1.254'`
- `app.host_ip('10.77.1.0/24', 0)` == `'10.77.1.10'`; index 5 → `.15`
- `app.hackerlab_ip('10.77.1.0/24')` == `'10.77.1.2'`
- `app.transit_subnet(0)` == `'10.250.0.0/29'`; index 3 → `'10.250.3.0/29'`
- `app.router_key('Core Node!')` == `'core-node'` (via normalize_identifier); `router_key('')` == `'router'`
- `app.transit_network_key('r1','r2')` == `'transit_r1_r2'`
- `app.network_router_ips({'cidr':'10.77.1.0/24'}, ['r1'])` == `{'r1':'10.77.1.254'}`
- two routers `['r1','r2']` → `{'r1':'10.77.1.254','r2':'10.77.1.253'}` (max(240,254-1))
- dedup + order preserved: `['r1','r1','r2']` → only r1,r2
- `app.normalize_identifier('Test Lab!', 'x')` == `'test-lab'`; `('','fb')` == `'fb'`; `(None,'fb')` == `'fb'`; `('UPPER_Case','')` == `'upper_case'`
- `app.build_router_maps` + `app.router_descendant_networks`: build a small topology with 2 routers (parent→child) and assert child counts + descendant network list. Construct the input dict, then call (no validation needed — build_router_maps reads `routers`/`networks` directly).

## File: test_model.py — validation, summarize, host_agents, normalize, slugify
- `validate_topology` happy path: feed a deep copy of MINIMAL, assert it returns a dict, that `networks[0].hosts[0]['image'] == 'ubuntu:24.04'`, `password == 'strato'`, `username == 'student'`, a default `routers` list was created (`len>=1`, first id `'router1'`), `monitoring.slips.enabled` is False, `infrastructure.hackerlab_network_id == 'net1'`.
- `validate_topology` rejects: non-dict (`{}`? no — non-dict → ValueError; pass a list), empty name, missing networks, >8 networks (build 9), network with no hosts, >24 hosts (build 25), duplicate network id. Each `pytest.raises(ValueError)`.
- `validate_topology` mutates in place: pass a copy, call it, assert the input dict was changed (e.g. host gained `agents` key).
- `host_agents`: `{'agents':['coder56']}` → `['coder56']`; legacy `{'agent_enabled':True,'agent_type':'coder56'}` → `['coder56']`; dedup `{'agents':['a','a','b']}` → `['a','b']`; none → `[]`.
- `slugify`: read its body (it wraps normalize_identifier); assert `slugify('Test Lab')` matches `normalize_identifier('Test Lab','')`.
- `summarize`: read body, assert returned dict has keys including `id`,`name` and that ids/fields match the input `minimal_topology`.
- `list_topologies`: write 2 fake topology.json files under `app.TOPOLOGIES_DIR` (use `save_topology` or write via `app.write_json` to `app.topology_path('x')`), call `list_topologies()`, assert count and that each entry is a summarize() dict. NOTE: `list_topologies` DELETES topologies named 'ssh lab' — add one and assert it's gone.

## File: test_scripts.py — shell emitters (golden-master the deterministic ones)
- `shell_quote("it's")` == `"'it'\"'\"'s'"`; `shell_quote(5)` == `"'5'"`.
- `ssh_setup_block('bob','pass')`: assert contains `mkdir -p /var/run/sshd`, `useradd -m -s /bin/bash 'bob'`, `chpasswd`, and the quoted `bob:pass`.
- `opencode_agent_block` with a NO-agent host (e.g. minimal host, agent_enabled False) → returns `''`.
- `opencode_agent_block` with an agent host (from `rich_topology`, the coder56 host) → non-empty string; assert it contains the guardrail delegation marker (`4097`) and the tools block keys (`"skill"`,`"task"`,`"bash"`). (Use structural asserts, not golden — output may include prompt text.)
- `host_script(minimal_topology, network, host, host_index, gateway)` → golden-master (`golden('host_script_minimal.txt', out)`). Read its signature precisely; network/host come from `minimal_topology['networks'][0]` and `['hosts'][0]`; gateway = `app.router_ip(network['cidr'])`.
- `router_script(...)` and `hackerlab_script(...)` → golden-master. Read signatures from app.py (router_script takes many args from build_router_maps; hackerlab_script(network, gateway)).
- `role_service_block('web-server')` and `('db')` → golden or substring (read body for expected service line).
- `default_data_for_host(...)` → golden-master.

## File: test_compose.py — generate_compose + naming + resolve_run_id
- `generate_compose(minimal_topology)` → golden-master the FULL dict (`golden('compose_minimal.json', out)`). Also assert structural: top-level has `'services'`; service names start with `scl-topology-`; each host has a `RUN_ID` env equal to the topology id (fresh outputs dir → no timestamp suffix); image is `ubuntu:24.04`.
- `generate_compose(rich_topology)` → golden-master (`compose_rich.json`).
- `resolve_run_id('topo1')` with fresh tmp outputs (autouse) → `'topo1'` (dir absent, no suffix). Then create `Path(app.OUTPUTS_HOST_PATH)/'topo1'` and call again → starts with `'topo1-'` and has the `-YYYYMMDD-HHMM` suffix (assert it != 'topo1' and matches regex `^topo1-\d{8}-\d{4}$`).
- `resolve_run_id` honors `RUN_ID` env (monkeypatch.setenv('RUN_ID','override-x')) → base becomes `'override-x'`.
- `compose_project_name('topo1')`, `topology_network_name('topo1','net1')`, `hackerlab_container_name('topo1')`, `resolve_topology_network_name('topo1','net1')` → read bodies, assert exact strings. (`resolve_topology_network_name` may query docker — if it shells out, monkeypatch `app.docker_inspect_json` to a fixed value and assert both branches.)

## File: test_store.py — persistence IO
- `read_json`/`write_json` roundtrip on tmp path: write a dict, read it back, equal.
- `read_json` on missing file → read body; if it returns `{}` assert that, else `pytest.raises`.
- `slugify`, `topology_dir/topology_path/compose_path`: assert paths under `app.TOPOLOGIES_DIR` for a known id.
- `save_topology`: deep-copy MINIMAL, call `save_topology(copy)`. Assert: returns dict with `id`, `created_at`, `updated_at`; file exists at `app.topology_path(ret['id'])`; `read_json` of it round-trips; compose file written at `app.compose_path(ret['id'])`. (save_topology also calls `is_running` which may shell to docker — should return False gracefully; if it raises, monkeypatch `app.is_running` to `lambda *_: False`.)

## Notes for all agents
- Read the function bodies in `app.py` before writing asserts; the line ranges are documented in the refactor plan but always confirm.
- If a function shells out to docker/network, monkeypatch the lowest-level helper (`app.docker_inspect_json`, `app.is_running`, `app.run_compose`) — never invoke real docker.
- Tests must pass on the CURRENT monolith. Do not write aspirational asserts.
