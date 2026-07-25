import json
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit

import app

_INDEX_PATH = Path(__file__).parent / 'templates' / 'index.html'


def _load_index_html():
    return _INDEX_PATH.read_text(encoding='utf8')


class TopologyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self.handle_get()
        except Exception as exc:
            self.send_json(500, {'error': str(exc)})

    def do_POST(self):
        try:
            self.handle_post()
        except Exception as exc:
            self.send_json(500, {'error': str(exc)})

    def handle_get(self):
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip('/') or '/'
        if path == '/':
            available_agents = {k: v.get('description', '') for k, v in app.AGENT_TEMPLATES.items()}
            html = _load_index_html().replace('__HOST_TYPES__', json.dumps(app.HOST_TYPES))
            html = html.replace('__AVAILABLE_AGENTS__', json.dumps(available_agents))
            self.send_html(html)
            return
        if path == '/health':
            self.send_json(200, {'status': 'healthy'})
            return
        if path == '/api/topologies':
            self.send_json(200, {'topologies': app.list_topologies()})
            return
        match = re.fullmatch(r'/api/jobs/([^/]+)', path)
        if match:
            job = app.get_job(unquote(match.group(1)))
            if not job:
                self.send_json(404, {'error': 'Job not found'})
                return
            self.send_json(200, {'job': job})
            return
        match = re.fullmatch(r'/api/topologies/([^/]+)', path)
        if match:
            topology_id = unquote(match.group(1))
            path_obj = app.topology_path(topology_id)
            if not path_obj.exists():
                self.send_json(404, {'error': 'Topology not found'})
                return
            self.send_json(200, {'topology': app.read_json(path_obj), 'running': app.is_running(topology_id)})
            return
        self.send_json(404, {'error': 'Not found'})

    def handle_post(self):
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip('/') or '/'
        if path == '/api/topologies':
            topology = app.save_topology(self.read_body())
            self.send_json(200, {'topology': topology})
            return
        if path == '/api/generate-data':
            body = self.read_body()
            job_id = app.start_job(lambda: {
                'content': app.generate_data_with_llm(body.get('topology') or {}, body.get('host') or {})
            })
            self.send_json(202, {'job_id': job_id})
            return
        match = re.fullmatch(r'/api/topologies/([^/]+)/(start|stop)', path)
        if match:
            topology_id = unquote(match.group(1))
            action = match.group(2)
            if action == 'start':
                job_id = app.start_job(lambda: app.start_topology(topology_id))
            else:
                job_id = app.start_job(lambda: app.stop_topology(topology_id))
            self.send_json(202, {'job_id': job_id})
            return
        self.send_json(404, {'error': 'Not found'})

    def read_body(self):
        length = int(self.headers.get('Content-Length') or '0')
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode('utf8'))

    def log_message(self, format_string, *args):
        print(format_string % args)

    def send_html(self, body):
        encoded = body.encode('utf8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, status_code, payload):
        encoded = json.dumps(payload).encode('utf8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
