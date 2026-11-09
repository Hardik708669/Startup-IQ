"""Opt-in minimal live smoke tests for configured AI providers.

Run with the project environment. No request is made for providers without keys.
"""
from __future__ import annotations

import os
import time

from AI.ai_service import _call_gemini_api, _call_openai_api, parse_ai_json_response


def _run(name: str, key: str | None) -> None:
    if not key:
        print(f"SKIPPED {name}: no API key configured")
        return
    started = time.perf_counter()
    try:
        if name == "openai":
            raw = _call_openai_api(key, "Return an empty valid Startup IQ JSON object.", timeout=15)
        else:
            raw = _call_gemini_api(key, "Return an empty valid Startup IQ JSON object.", timeout=15)
        parse_ai_json_response(raw, provider_name=name)
        print(f"PASS {name}: model request and structured response validated; latency_ms={round((time.perf_counter()-started)*1000, 1)}")
    except Exception as exc:
        print(f"FAIL {name}: {type(exc).__name__}; latency_ms={round((time.perf_counter()-started)*1000, 1)}")


if __name__ == "__main__":
    _run("openai", os.getenv("OPENAI_API_KEY"))
    _run("gemini", os.getenv("GEMINI_API_KEY"))
