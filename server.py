import signal
import threading

import app


def handle_shutdown(signum, _frame):
    print(f'Received signal {signum}, shutting down network topology plugin.')
    if app.SERVER is not None:
        threading.Thread(target=app.SERVER.shutdown, daemon=True).start()


def main():
    app.TOPOLOGIES_DIR.mkdir(parents=True, exist_ok=True)
    app.SERVER = app.ThreadingHTTPServer((app.HOST, app.PORT), app.TopologyHandler)
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    print(f'Network topology plugin listening on http://{app.HOST}:{app.PORT}')
    try:
        app.SERVER.serve_forever()
    finally:
        app.SERVER.server_close()
        print('Network topology plugin stopped.')
