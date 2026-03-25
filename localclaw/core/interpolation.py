"""Shared interpolation helpers for planning and execution."""

import re
from typing import Any, Dict

from jinja2 import Template


_SIMPLE_JINJA_VAR = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")
_SIMPLE_DOLLAR_VAR = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
_SIMPLE_DOLLAR_BRACE_VAR = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_DOLLAR_BRACE_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def normalize_condition_expression(expression: Any) -> Any:
    """Normalize OpenClaw-style templated conditions into Python expressions."""
    if not isinstance(expression, str):
        return expression

    normalized = expression.strip()
    if normalized.startswith("{{") and normalized.endswith("}}"):
        normalized = normalized[2:-2].strip()
    elif normalized.startswith("${") and normalized.endswith("}"):
        normalized = normalized[2:-1].strip()
    return normalized or None


def resolve_interpolated_value(value: Any, variables: Dict[str, Any]) -> Any:
    """Resolve $var, ${var}, and Jinja-like values recursively."""
    if isinstance(value, dict):
        return {
            key: resolve_interpolated_value(item, variables)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [resolve_interpolated_value(item, variables) for item in value]

    if not isinstance(value, str):
        return value

    simple_jinja = _SIMPLE_JINJA_VAR.match(value)
    if simple_jinja:
        return variables.get(simple_jinja.group(1), "")

    simple_dollar = _SIMPLE_DOLLAR_VAR.match(value)
    if simple_dollar:
        return variables.get(simple_dollar.group(1), "")

    simple_dollar_brace = _SIMPLE_DOLLAR_BRACE_VAR.match(value)
    if simple_dollar_brace:
        return variables.get(simple_dollar_brace.group(1), "")

    rendered = value
    if "{{" in rendered:
        rendered = Template(rendered).render(**variables)
    if "${" in rendered:
        rendered = _DOLLAR_BRACE_VAR.sub(
            lambda match: str(variables.get(match.group(1), "")),
            rendered,
        )
    return rendered
