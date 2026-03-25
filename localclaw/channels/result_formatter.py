"""Helpers for turning task results into chat-friendly text."""

from __future__ import annotations

import html
import json
from typing import Any, Dict, Iterable, Optional
from xml.etree import ElementTree

from localclaw.core.models import ExecutionResult, Task, TaskState


def format_task_for_chat(task: Task) -> str:
    """Format a task result into a user-facing chat reply."""

    if task.state == TaskState.VERIFYING:
        result = task.result
        if isinstance(result, ExecutionResult) and result.message:
            return result.message
        return "Waiting for approval"

    if task.state == TaskState.FAILED:
        return f"Error: {task.error or 'unknown error'}"

    result = task.result
    if isinstance(result, ExecutionResult):
        text = _format_result_payload(result.data or {}, task)
        if text:
            return text
        if result.message and result.message != "Task completed successfully":
            return result.message
        if result.data:
            return _stringify_value(result.data)
        return result.message or ""

    if isinstance(result, dict):
        text = _format_result_payload(result, task)
        if text:
            return text
        return _stringify_value(result)

    return str(result or "")


def _format_result_payload(payload: Dict[str, Any], task: Optional[Task]) -> Optional[str]:
    """Try to format a result payload, preferring the most user-facing step."""

    if not payload:
        return None

    direct = _format_single_output(payload, task)
    if direct:
        return direct

    ordered_outputs = list(_iter_step_outputs(payload, task))
    for output in reversed(ordered_outputs):
        formatted = _format_single_output(output, task)
        if formatted:
            return formatted

    if len(payload) == 1:
        first_value = next(iter(payload.values()))
        return _stringify_value(first_value)

    return None


def _iter_step_outputs(payload: Dict[str, Any], task: Optional[Task]) -> Iterable[Dict[str, Any]]:
    """Yield step outputs in plan order when possible."""

    if task and task.plan:
        for step in task.plan.steps:
            output = payload.get(step.id)
            if isinstance(output, dict):
                yield output
        return

    for value in payload.values():
        if isinstance(value, dict):
            yield value


def _format_single_output(output: Dict[str, Any], task: Optional[Task]) -> Optional[str]:
    """Format a single step output dict."""

    if not isinstance(output, dict):
        return None

    for key in ("result", "message", "content"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            if key == "message" and value == "Task completed successfully":
                continue
            return value

    if "stdout" in output or "stderr" in output:
        return _format_shell_output(output)

    if "free_bytes" in output and "total_bytes" in output:
        return _format_disk_usage_output(output)

    if "files" in output or "directories" in output:
        return _format_file_list_output(output, task)

    if "body" in output:
        return _format_http_output(output, task)

    return None


def _format_shell_output(output: Dict[str, Any]) -> str:
    """Format shell-like tool output."""

    parts = []
    command = str(output.get("command") or "").strip()
    stdout = str(output.get("stdout") or "").strip()
    stderr = str(output.get("stderr") or "").strip()

    if command:
        parts.append(f"Command: {command}")
    if "exit_code" in output:
        parts.append(f"Exit code: {output['exit_code']}")
    if stdout:
        parts.append(f"STDOUT:\n{stdout}")
    if stderr:
        parts.append(f"STDERR:\n{stderr}")

    return "\n\n".join(parts) if parts else _stringify_value(output)


def _format_file_list_output(output: Dict[str, Any], task: Optional[Task]) -> str:
    """Format a directory listing."""

    lines = []
    path = str(output.get("path") or "").strip()
    if path:
        lines.append(f"Path: {path}")

    directories = output.get("directories") or []
    files = output.get("files") or []
    folders_only = _wants_directory_only_view(task)
    if directories:
        lines.append("Directories:")
        lines.extend(f"- {item}" for item in directories)
    if files and not folders_only:
        lines.append("Files:")
        lines.extend(f"- {item}" for item in files)
    if folders_only and not directories:
        lines.append("(no directories)")
    elif not directories and not files:
        lines.append("(empty)")

    return "\n".join(lines)


def _format_disk_usage_output(output: Dict[str, Any]) -> str:
    """Format disk capacity output into a readable summary."""

    lines = []
    path = str(output.get("path") or "").strip()
    total = str(output.get("total") or "").strip()
    used = str(output.get("used") or "").strip()
    free = str(output.get("free") or "").strip()
    free_percent = output.get("free_percent")
    used_percent = output.get("used_percent")

    if path:
        lines.append(f"Path: {path}")
    if free:
        if free_percent not in (None, ""):
            lines.append(f"Free: {free} ({free_percent}%)")
        else:
            lines.append(f"Free: {free}")
    if used:
        if used_percent not in (None, ""):
            lines.append(f"Used: {used} ({used_percent}%)")
        else:
            lines.append(f"Used: {used}")
    if total:
        lines.append(f"Total: {total}")

    return "\n".join(lines) if lines else _stringify_value(output)


def _wants_directory_only_view(task: Optional[Task]) -> bool:
    """Return True when the user explicitly asked for folders/directories only."""

    if not task or not task.intent:
        return False

    if task.intent.intent == "list_folders":
        return True

    raw_flag = task.intent.params.get("folders_only")
    if isinstance(raw_flag, bool):
        return raw_flag
    return str(raw_flag or "").strip().lower() in {"1", "true", "yes"}


def _format_http_output(output: Dict[str, Any], task: Optional[Task]) -> str:
    """Format HTTP tool output, with special handling for weather responses."""

    body = output.get("body")
    if isinstance(body, dict):
        weather_text = _format_weather_payload(body, task)
        if weather_text:
            return weather_text
        return _stringify_value(body)
    if isinstance(body, list):
        return _stringify_value(body)
    if isinstance(body, str):
        rss_text = _format_rss_payload(body, task)
        if rss_text:
            return rss_text
    if body not in (None, ""):
        return str(body)
    return _stringify_value(output)


def _format_rss_payload(body: str, task: Optional[Task]) -> Optional[str]:
    """Render RSS/Atom headline feeds into a short readable summary."""

    if not _looks_like_xml_document(body):
        return None

    try:
        root = ElementTree.fromstring(body.strip())
    except ElementTree.ParseError:
        return None

    items = []
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        title = _coerce_feed_text(_find_child_text(node, "title"))
        link = _find_feed_link(node)
        source = _coerce_feed_text(_find_child_text(node, "source"))
        if not title:
            continue
        cleaned_title = title
        if source and cleaned_title.endswith(f" - {source}"):
            cleaned_title = cleaned_title[: -(len(source) + 3)].rstrip()
        items.append(
            {
                "title": cleaned_title,
                "source": source,
                "link": link,
            }
        )

    if not items:
        return None

    limit = _resolve_news_limit(task)
    selected = items[:limit]
    topic = _resolve_news_topic(task)
    header = f"{topic}最新新闻（{len(selected)}条）" if topic else f"最新新闻（{len(selected)}条）"
    lines = [header]
    for index, item in enumerate(selected, start=1):
        line = f"{index}. {item['title']}"
        if item["source"]:
            line += f" | {item['source']}"
        lines.append(line)
        if item["link"]:
            lines.append(f"   {item['link']}")
    return "\n".join(lines)


def _looks_like_xml_document(text: str) -> bool:
    """Return True when the response body looks like XML/RSS."""

    preview = text.lstrip()[:200].lower()
    return preview.startswith("<?xml") or preview.startswith("<rss") or preview.startswith("<feed")


def _resolve_news_limit(task: Optional[Task]) -> int:
    """Resolve the desired number of headlines from task intent params."""

    params = task.intent.params if task and task.intent else {}
    raw_limit = params.get("limit") or params.get("count")
    try:
        return max(1, min(int(raw_limit), 20))
    except (TypeError, ValueError):
        return 10


def _resolve_news_topic(task: Optional[Task]) -> str:
    """Resolve the topic label used for RSS headline summaries."""

    params = task.intent.params if task and task.intent else {}
    return str(params.get("topic") or "").strip()


def _find_child_text(node: ElementTree.Element, name: str) -> str:
    """Find the first direct child text matching a local tag name."""

    for child in node:
        if _local_name(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""


def _find_feed_link(node: ElementTree.Element) -> str:
    """Find the best link value for an RSS item or Atom entry."""

    for child in node:
        if _local_name(child.tag) != "link":
            continue
        href = str(child.attrib.get("href") or "").strip()
        if href:
            return href
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return ""


def _coerce_feed_text(value: str) -> str:
    """Normalize feed text for chat output."""

    return html.unescape(str(value or "").strip())


def _local_name(tag: str) -> str:
    """Return a namespace-agnostic XML local tag name."""

    return str(tag or "").rsplit("}", 1)[-1].lower()


def _format_weather_payload(body: Dict[str, Any], task: Optional[Task]) -> Optional[str]:
    """Render wttr.in JSON into a short Chinese weather reply."""

    forecast_days = body.get("weather")
    current_condition = body.get("current_condition")
    if not isinstance(forecast_days, list) or not forecast_days:
        if not isinstance(current_condition, list):
            return None

    day_offset, day_label = _resolve_weather_day_context(task)
    if not isinstance(forecast_days, list) or not forecast_days:
        current = current_condition[0] if isinstance(current_condition, list) and current_condition else {}
        description = _extract_weather_desc(current)
        temp_c = current.get("temp_C")
        location = _resolve_weather_display_location(body, task)
        parts = []
        if location:
            parts.append(f"地点：{location}")
        summary = "当前天气"
        if description:
            summary += f"：{description}"
        if temp_c not in (None, ""):
            summary += f"，{temp_c}°C"
        parts.insert(0, summary)
        return "。".join(part for part in parts if part)

    index = max(0, min(day_offset, len(forecast_days) - 1))
    target_day = forecast_days[index] or {}
    hourly = target_day.get("hourly") or []
    reference_hour = _pick_reference_hour(hourly)
    description = _extract_weather_desc(reference_hour) or _extract_weather_desc(target_day)
    chance_of_rain = _max_numeric_field(hourly, "chanceofrain")
    precip_mm = _max_numeric_field(hourly, "precipMM", as_float=True)
    location = _resolve_weather_display_location(body, task)
    min_temp = target_day.get("mintempC") or target_day.get("avgtempC")
    max_temp = target_day.get("maxtempC") or target_day.get("avgtempC")

    headline = _build_rain_headline(day_label, description, chance_of_rain, precip_mm)
    details = []
    if location:
        details.append(f"地点：{location}")
    if description:
        details.append(f"天气：{description.strip()}")
    if chance_of_rain is not None:
        details.append(f"降雨概率：{chance_of_rain}%")
    if min_temp not in (None, "") and max_temp not in (None, ""):
        details.append(f"气温：{min_temp} 到 {max_temp}°C")

    return "。".join([headline, *details]) if details else headline


def _resolve_weather_day_context(task: Optional[Task]) -> tuple[int, str]:
    """Infer which forecast day the user asked about."""

    params = task.intent.params if task and task.intent else {}
    raw_message = ""
    if task and task.message:
        raw_message = task.message.content or ""

    raw_offset = params.get("day_offset")
    try:
        day_offset = int(raw_offset)
    except (TypeError, ValueError):
        if "后天" in raw_message:
            day_offset = 2
        elif "明天" in raw_message:
            day_offset = 1
        else:
            day_offset = 0

    day_label = str(params.get("day_label") or "").strip()
    if not day_label:
        day_label = {0: "今天", 1: "明天", 2: "后天"}.get(day_offset, "这天")

    return day_offset, day_label


def _pick_reference_hour(hourly: Any) -> Dict[str, Any]:
    """Pick the hour closest to midday for a readable forecast summary."""

    if not isinstance(hourly, list) or not hourly:
        return {}

    def distance(item: Dict[str, Any]) -> int:
        raw_time = str(item.get("time") or "1200").strip() or "1200"
        try:
            numeric = int(raw_time)
        except ValueError:
            numeric = 1200
        return abs(numeric - 1200)

    return min((item for item in hourly if isinstance(item, dict)), key=distance, default={})


def _extract_weather_desc(item: Any) -> str:
    """Extract the localized weather description from wttr.in structures."""

    if not isinstance(item, dict):
        return ""
    desc = item.get("weatherDesc")
    if isinstance(desc, list) and desc:
        first = desc[0]
        if isinstance(first, dict):
            value = first.get("value")
            if value:
                return str(value)
    value = item.get("weather")
    return str(value or "").strip()


def _extract_weather_location(body: Dict[str, Any]) -> str:
    """Extract the nearest area label from wttr.in JSON."""

    nearest_area = body.get("nearest_area")
    if not isinstance(nearest_area, list) or not nearest_area:
        return ""

    first = nearest_area[0] if isinstance(nearest_area[0], dict) else {}
    area_name = ""
    country = ""
    if isinstance(first.get("areaName"), list) and first["areaName"]:
        area_name = str((first["areaName"][0] or {}).get("value") or "").strip()
    if isinstance(first.get("country"), list) and first["country"]:
        country = str((first["country"][0] or {}).get("value") or "").strip()

    if area_name and country and country.lower() not in area_name.lower():
        return f"{area_name}, {country}"
    return area_name or country


def _resolve_weather_display_location(body: Dict[str, Any], task: Optional[Task]) -> str:
    """Prefer the user-requested weather location over wttr.in's nearest-area label."""

    requested_location = ""
    if task and task.intent:
        requested_location = str(task.intent.params.get("location") or "").strip()
    return requested_location or _extract_weather_location(body)


def _max_numeric_field(items: Any, key: str, as_float: bool = False) -> Optional[float]:
    """Return the maximum numeric value for a field across forecast items."""

    if not isinstance(items, list):
        return None

    values = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_value = item.get(key)
        if raw_value in (None, ""):
            continue
        try:
            values.append(float(raw_value) if as_float else int(float(raw_value)))
        except (TypeError, ValueError):
            continue

    if not values:
        return None
    return max(values)


def _build_rain_headline(
    day_label: str,
    description: str,
    chance_of_rain: Optional[float],
    precip_mm: Optional[float],
) -> str:
    """Build the yes/no style answer for rain questions."""

    desc_lower = description.lower()
    rainy_desc = any(
        token in desc_lower
        for token in ("rain", "shower", "drizzle", "thunder", "storm", "sleet")
    )
    heavy_signal = (
        rainy_desc
        or (chance_of_rain is not None and chance_of_rain >= 60)
        or (precip_mm is not None and precip_mm >= 1.0)
    )
    dry_signal = (
        not rainy_desc
        and (chance_of_rain is not None and chance_of_rain <= 20)
        and (precip_mm is None or precip_mm <= 0.1)
    )

    if heavy_signal:
        return f"{day_label}大概率会下雨"
    if dry_signal:
        return f"{day_label}看起来不会下雨"
    return f"{day_label}有一定下雨可能"


def _stringify_value(value: Any) -> str:
    """Convert structured values into readable text."""

    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)
