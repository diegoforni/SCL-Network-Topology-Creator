import threading
import uuid

import app


def start_job(task):
    job_id = uuid.uuid4().hex
    with app.JOBS_LOCK:
        app.JOBS[job_id] = {'id': job_id, 'status': 'running', 'result': None, 'error': ''}

    def worker():
        try:
            result = task()
        except Exception as exc:  # pragma: no cover - depends on Docker and Ollama runtime
            with app.JOBS_LOCK:
                app.JOBS[job_id]['status'] = 'failed'
                app.JOBS[job_id]['error'] = str(exc)
        else:
            with app.JOBS_LOCK:
                app.JOBS[job_id]['status'] = 'completed'
                app.JOBS[job_id]['result'] = result or {}

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def get_job(job_id):
    with app.JOBS_LOCK:
        return dict(app.JOBS.get(job_id) or {})
