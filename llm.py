"""Thin client for the Meta Model API (OpenAI-compatible chat completions).

Settings come from environment variables:
  MODEL_API_KEY   your key from the Meta Model API dashboard
  MODEL_API_BASE  default https://api.meta.ai/v1
  MODEL_NAME      default muse-spark-1.3 (try muse-spark-1.2 if 1.3 is not enabled for you)
"""
import json
import os
import re

import requests

import pathlib


def _load_env():
    """Read KEY=value lines from a .env file next to this script (no extra package needed)."""
    f = pathlib.Path(__file__).parent / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

BASE = os.getenv("MODEL_API_BASE", "https://api.meta.ai/v1").rstrip("/")
MODEL = os.getenv("MODEL_NAME", "muse-spark-1.3")


def key():
    return os.getenv("MODEL_API_KEY", "").strip()


def available():
    return bool(key())


class LLMError(Exception):
    pass


def parse_json(text):
    """Pull a JSON object out of a model reply (handles code fences and chatter)."""
    if not isinstance(text, str):
        raise LLMError("empty reply")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError("model did not return valid JSON")


# Muse models always reason before answering; "reasoning_effort" controls how long.
# Lower effort = much faster. Override in .env: MODEL_EXTRACT_EFFORT, MODEL_ROUTE_EFFORT, MODEL_REASONING.
DEFAULT_EFFORT = os.getenv("MODEL_REASONING", "low")
EXTRACT_EFFORT = os.getenv("MODEL_EXTRACT_EFFORT", "minimal")
ROUTE_EFFORT = os.getenv("MODEL_ROUTE_EFFORT", "low")
LAST_USAGE = {}


def _post(messages, json_mode, effort, timeout):
    body = {"model": MODEL, "messages": messages, "temperature": 0.2}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if effort:
        body["reasoning_effort"] = effort
    return requests.post(f"{BASE}/chat/completions", json=body, timeout=timeout,
                         headers={"Authorization": f"Bearer {key()}",
                                  "Content-Type": "application/json"})


def chat(messages, json_mode=True, timeout=90, effort=None):
    """Return the text of the first choice.
    If the API rejects an optional setting (HTTP 400) it retries with a simpler request."""
    global LAST_USAGE
    if not available():
        raise LLMError("MODEL_API_KEY is not set")
    effort = DEFAULT_EFFORT if effort is None else effort
    variants = [(json_mode, effort)]
    if effort:
        variants.append((json_mode, None))
    if json_mode:
        variants.append((False, None))
    try:
        r = None
        for jm, ef in variants:
            r = _post(messages, jm, ef, timeout)
            if r.status_code != 400:
                break
        if r.status_code >= 400:
            raise LLMError(f"API error {r.status_code}: {r.text[:300]}")
        data = r.json()
        LAST_USAGE = data.get("usage") or {}
        return data["choices"][0]["message"]["content"]
    except requests.RequestException as e:
        raise LLMError(f"network error: {e}") from e
    except (KeyError, IndexError, ValueError) as e:
        raise LLMError(f"unexpected API response: {e}") from e


def chat_json(messages, timeout=90, effort=None):
    """Ask for JSON, parse it, and retry once with a stricter reminder if it fails."""
    text = chat(messages, True, timeout, effort)
    try:
        return parse_json(text)
    except LLMError:
        retry = messages + [{"role": "assistant", "content": text},
                            {"role": "user", "content": "Return valid JSON only, nothing else."}]
        return parse_json(chat(retry, True, timeout, effort))
