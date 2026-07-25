import json

import app


def topology_dir(topology_id):
    return app.TOPOLOGIES_DIR / topology_id


def topology_path(topology_id):
    return topology_dir(topology_id) / 'topology.json'


def compose_path(topology_id):
    return topology_dir(topology_id) / 'docker-compose.yml'


def read_json(path):
    with open(path, 'r', encoding='utf8') as file:
        return json.load(file)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf8') as file:
        json.dump(payload, file, indent=2, sort_keys=True)
        file.write('\n')
