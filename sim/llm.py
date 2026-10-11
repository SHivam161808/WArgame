"""Optional LLM backend — a self-hosted FOSS model behind an OpenAI-compatible endpoint
(Ollama ≥ 0.1.14, llama.cpp server, vLLM, LocalAI). C-1 forbids paid APIs, so there is no
default remote endpoint: with LLM_BASE_URL unset every AI feature uses the built-in analyst.
"""
from __future__ import annotations

import json
import os
import urllib.request

BASE_URL = os.environ.get('LLM_BASE_URL', '').rstrip('/')
MODEL = os.environ.get('LLM_MODEL', 'llama3.1:8b')
TIMEOUT = float(os.environ.get('LLM_TIMEOUT_S', '20'))


def available() -> bool:
    return bool(BASE_URL)


def complete(system: str, user: str, max_tokens: int = 500, temperature: float = 0.3) -> str | None:
    """Return the model's reply, or None on any failure (callers fall back to the analyst)."""
    if not BASE_URL:
        return None
    body = json.dumps({
        'model': MODEL,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'max_tokens': max_tokens,
        'temperature': temperature,
        'stream': False,
    }).encode()
    req = urllib.request.Request(f'{BASE_URL}/v1/chat/completions', data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
        return data['choices'][0]['message']['content'].strip()
    except Exception:
        return None
