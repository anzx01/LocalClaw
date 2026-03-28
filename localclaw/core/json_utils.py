"""Helpers for extracting structured JSON from noisy model outputs."""

from __future__ import annotations

import json
import re
from typing import Optional


_FENCED_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_last_json_object(content: str) -> Optional[str]:
    """Return the last complete JSON object found in model output text.

    Some local models emit reasoning, examples, or ``<think>`` traces before the
    final JSON object. We scan the full output and any fenced code blocks, then
    keep the dict-shaped JSON object that ends furthest to the right.
    """

    normalized = str(content or "").strip()
    if not normalized:
        return None

    decoder = json.JSONDecoder()
    candidates = [normalized]
    candidates.extend(match.group(1).strip() for match in _FENCED_BLOCK_RE.finditer(normalized))

    best_start = -1
    best_end = -1
    best_value: Optional[str] = None

    for candidate in candidates:
        for start, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, end = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue

            absolute_end = start + end
            if absolute_end > best_end or (absolute_end == best_end and (best_start < 0 or start < best_start)):
                best_start = start
                best_end = absolute_end
                best_value = candidate[start:absolute_end]

    return best_value
