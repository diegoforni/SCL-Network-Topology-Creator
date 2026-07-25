import json
import re
from urllib import error as urllib_error
from urllib import request as urllib_request

import app


def generate_data_with_llm(topology, host):
    prompt = host.get('data_prompt') or (
        f"Generate realistic but fictional training data for a {app.HOST_TYPES[host['type']]['label']} host "
        f"inside a cyber range topology named {topology.get('name', 'training lab')}."
    )
    message = {
        'role': 'user',
        'content': (
            "/no_think\n"
            "Create concise, realistic, fictional data for a local cyber range host. "
            "Do not include real secrets, real people, or harmful instructions. "
            "Return plain text only.\n\n"
            f"Host: {host.get('name')}\n"
            f"Role: {app.HOST_TYPES[host['type']]['label']}\n"
            f"Requested data: {prompt}"
        )
    }
    encoded = json.dumps([message]).encode('utf8')
    request = urllib_request.Request(
        app.LLM_URL,
        data=encoded,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib_request.urlopen(request, timeout=120) as response:
            messages = json.loads(response.read().decode('utf8'))
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode('utf8', errors='replace').strip()
        if 'All connection attempts failed' in detail:
            detail += '. Start the SCL Ollama service with: docker compose up -d ollama'
        raise RuntimeError(detail or f'SCL LLM request failed with HTTP {exc.code}') from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f'Could not reach the SCL dashboard LLM endpoint: {exc.reason}') from exc
    if isinstance(messages, list) and messages:
        content = str(messages[-1].get('content') or '')
        return re.sub(r'<thought>.*?</thought>', '', content, flags=re.DOTALL).strip()
    return ''
