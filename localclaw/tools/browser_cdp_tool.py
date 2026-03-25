"""Browser CDP tool adapted from the upstream web-access skill."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from localclaw.config.settings import get_settings
from localclaw.core.models import ErrorType, ExecutionResult, RiskLevel
from localclaw.llm.provider import get_llm_provider
from localclaw.tools.base import Tool, ToolError, register_tool


logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)


class _BrowserProxyClient:
    """Small async client for the upstream CDP HTTP proxy."""

    def __init__(self, base_url: str = "http://127.0.0.1:3456", timeout: float = 20.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}{path}", params=params)
            response.raise_for_status()
            return response.json()

    async def post_json(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[str] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            headers: Dict[str, str] = {}
            content: Any = None
            if json_data is not None:
                headers["Content-Type"] = "application/json"
                content = json.dumps(json_data, ensure_ascii=False)
            elif body is not None:
                headers["Content-Type"] = "text/plain; charset=utf-8"
                content = body
            response = await client.post(
                f"{self._base_url}{path}",
                params=params,
                content=content,
                headers=headers or None,
            )
            response.raise_for_status()
            return response.json()

    async def health(self) -> Dict[str, Any]:
        payload = await self.get_json("/health")
        return payload if isinstance(payload, dict) else {"ok": False, "connected": False}

    async def targets(self) -> List[Dict[str, Any]]:
        payload = await self.get_json("/targets")
        return payload if isinstance(payload, list) else []

    async def new_tab(self, url: str) -> Dict[str, Any]:
        payload = await self.get_json("/new", params={"url": url})
        return payload if isinstance(payload, dict) else {}

    async def info(self, target: str) -> Dict[str, Any]:
        payload = await self.get_json("/info", params={"target": target})
        return payload if isinstance(payload, dict) else {}

    async def navigate(self, target: str, url: str) -> Dict[str, Any]:
        payload = await self.get_json("/navigate", params={"target": target, "url": url})
        return payload if isinstance(payload, dict) else {}

    async def back(self, target: str) -> Dict[str, Any]:
        payload = await self.get_json("/back", params={"target": target})
        return payload if isinstance(payload, dict) else {}

    async def close(self, target: str) -> Dict[str, Any]:
        payload = await self.get_json("/close", params={"target": target})
        return payload if isinstance(payload, dict) else {}

    async def scroll(
        self,
        target: str,
        direction: Optional[str] = None,
        y: Optional[int] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"target": target}
        if direction:
            params["direction"] = direction
        if y is not None:
            params["y"] = y
        payload = await self.get_json("/scroll", params=params)
        return payload if isinstance(payload, dict) else {}

    async def eval(self, target: str, script: str) -> Dict[str, Any]:
        payload = await self.post_json("/eval", params={"target": target}, body=script)
        return payload if isinstance(payload, dict) else {}

    async def click(self, target: str, selector: str) -> Dict[str, Any]:
        payload = await self.post_json("/click", params={"target": target}, body=selector)
        return payload if isinstance(payload, dict) else {}

    async def click_at(self, target: str, selector: str) -> Dict[str, Any]:
        payload = await self.post_json("/clickAt", params={"target": target}, body=selector)
        return payload if isinstance(payload, dict) else {}

    async def set_files(self, target: str, selector: str, files: List[str]) -> Dict[str, Any]:
        payload = await self.post_json(
            "/setFiles",
            params={"target": target},
            json_data={"selector": selector, "files": files},
        )
        return payload if isinstance(payload, dict) else {}

    async def screenshot(
        self,
        target: str,
        file_path: str,
        image_format: str = "png",
    ) -> Dict[str, Any]:
        payload = await self.get_json(
            "/screenshot",
            params={"target": target, "file": file_path, "format": image_format},
        )
        return payload if isinstance(payload, dict) else {}


class BrowserCDPTool(Tool):
    """Use the upstream CDP proxy as a LocalClaw-native browser tool."""

    name = "browser_cdp"
    description = "Use the local Chrome browser through a CDP proxy for real web access and page interaction"
    risk_level = RiskLevel.HIGH
    inputs = {"request": "string"}
    outputs = {"message": "string", "details": "dict"}

    DEFAULT_MAX_STEPS = 6
    MAX_FETCH_CHARS = 6000

    def validate_inputs(self, kwargs: Dict[str, Any]) -> List[str]:
        """Allow either high-level request mode or explicit low-level action mode."""

        errors: List[str] = []
        action = str(kwargs.get("action") or "agent").strip().lower()
        request = kwargs.get("request")

        if action == "agent":
            if not isinstance(request, str) or not request.strip():
                errors.append("Missing required parameter: request")
        elif action not in {
            "check",
            "targets",
            "new_tab",
            "info",
            "navigate",
            "back",
            "eval",
            "click",
            "click_at",
            "set_files",
            "scroll",
            "close",
            "screenshot",
            "fetch",
        }:
            errors.append(f"Unsupported action: {action}")

        return errors

    async def execute(
        self,
        request: str = "",
        action: str = "agent",
        url: Optional[str] = None,
        target: Optional[str] = None,
        script: Optional[str] = None,
        selector: Optional[str] = None,
        files: Optional[List[str]] = None,
        direction: Optional[str] = None,
        y: Optional[int] = None,
        file_path: Optional[str] = None,
        fetch_mode: str = "http",
        image_format: str = "png",
        max_steps: int = DEFAULT_MAX_STEPS,
        **kwargs: Any,
    ) -> ExecutionResult:
        """Execute browser operations through a managed CDP proxy."""

        del kwargs

        normalized_action = (action or "agent").strip().lower()
        if normalized_action == "agent":
            return await self._execute_agent_mode(
                request=request.strip(),
                url=(url or "").strip() or None,
                max_steps=max(1, min(int(max_steps or self.DEFAULT_MAX_STEPS), 10)),
            )

        return await self._execute_primitive_mode(
            action=normalized_action,
            url=(url or "").strip() or None,
            target=(target or "").strip() or None,
            script=script or "",
            selector=(selector or "").strip() or None,
            files=files or [],
            direction=(direction or "").strip() or None,
            y=y,
            file_path=(file_path or "").strip() or None,
            fetch_mode=(fetch_mode or "http").strip().lower(),
            image_format=(image_format or "png").strip().lower(),
        )

    async def _execute_primitive_mode(
        self,
        *,
        action: str,
        url: Optional[str],
        target: Optional[str],
        script: str,
        selector: Optional[str],
        files: List[str],
        direction: Optional[str],
        y: Optional[int],
        file_path: Optional[str],
        fetch_mode: str,
        image_format: str,
    ) -> ExecutionResult:
        """Run a direct low-level proxy action."""

        proxy = _BrowserProxyClient()
        readiness = await self._ensure_proxy_ready(start_proxy=action != "check")
        if action == "check":
            return ExecutionResult.success(message=readiness["message"], data=readiness)

        if not readiness.get("connected"):
            return ExecutionResult.from_error(
                readiness["message"],
                ErrorType.TOOL_ERROR,
                data=readiness,
            )

        try:
            if action == "targets":
                payload = {"targets": await proxy.targets()}
            elif action == "new_tab":
                self._require_value(url, "url")
                payload = await proxy.new_tab(url or "")
            elif action == "info":
                self._require_value(target, "target")
                payload = await proxy.info(target or "")
            elif action == "navigate":
                self._require_value(target, "target")
                self._require_value(url, "url")
                payload = await proxy.navigate(target or "", url or "")
            elif action == "back":
                self._require_value(target, "target")
                payload = await proxy.back(target or "")
            elif action == "eval":
                self._require_value(target, "target")
                self._require_value(script, "script")
                payload = await proxy.eval(target or "", script)
            elif action == "click":
                self._require_value(target, "target")
                self._require_value(selector, "selector")
                payload = await proxy.click(target or "", selector or "")
            elif action == "click_at":
                self._require_value(target, "target")
                self._require_value(selector, "selector")
                payload = await proxy.click_at(target or "", selector or "")
            elif action == "set_files":
                self._require_value(target, "target")
                self._require_value(selector, "selector")
                if not files:
                    raise ToolError("files must not be empty", ErrorType.VALIDATION_ERROR)
                payload = await proxy.set_files(target or "", selector or "", files)
            elif action == "scroll":
                self._require_value(target, "target")
                payload = await proxy.scroll(target or "", direction=direction, y=y)
            elif action == "close":
                self._require_value(target, "target")
                payload = await proxy.close(target or "")
            elif action == "screenshot":
                self._require_value(target, "target")
                resolved_path = file_path or str(get_settings().data_dir / "browser_cdp.png")
                payload = await proxy.screenshot(target or "", resolved_path, image_format=image_format)
                payload["file_path"] = resolved_path
            elif action == "fetch":
                self._require_value(url, "url")
                payload = await self._fetch_url(url or "", fetch_mode)
            else:
                raise ToolError(f"Unsupported action: {action}", ErrorType.VALIDATION_ERROR)
        except httpx.HTTPError as exc:
            logger.error("browser_cdp proxy request failed: %s", exc)
            return ExecutionResult.from_error(str(exc), ErrorType.TOOL_ERROR)

        return ExecutionResult.success(
            message=f"browser_cdp {action} completed",
            data={"message": f"browser_cdp {action} completed", **(payload if isinstance(payload, dict) else {"result": payload})},
        )

    async def _execute_agent_mode(self, *, request: str, url: Optional[str], max_steps: int) -> ExecutionResult:
        """Let the local model drive the browser in a bounded loop."""

        provider = get_llm_provider()
        if not await provider.is_available():
            return ExecutionResult.from_error(
                "Local model is unavailable, so web-access cannot plan browser actions.",
                ErrorType.SYSTEM_ERROR,
            )

        readiness = await self._ensure_proxy_ready(start_proxy=True)
        if not readiness.get("connected"):
            return ExecutionResult.from_error(
                readiness["message"],
                ErrorType.TOOL_ERROR,
                data=readiness,
            )

        proxy = _BrowserProxyClient()
        explicit_urls: List[str] = []
        if url:
            explicit_urls.append(url)
        for match in _URL_PATTERN.findall(request):
            normalized = match.rstrip(".,)")
            if normalized not in explicit_urls:
                explicit_urls.append(normalized)

        created_targets: List[str] = []
        current_target: Optional[str] = None
        observations: List[Dict[str, Any]] = []
        final_answer = ""

        try:
            if explicit_urls:
                seed = await proxy.new_tab(explicit_urls[0])
                seed_target = str(seed.get("targetId") or "").strip()
                if seed_target:
                    created_targets.append(seed_target)
                    current_target = seed_target
                    info = await proxy.info(seed_target)
                    observations.append({"kind": "seed_tab", "target": seed_target, "details": info})

            for step_index in range(1, max_steps + 1):
                state = await self._build_agent_state(
                    proxy=proxy,
                    created_targets=created_targets,
                    current_target=current_target,
                    request=request,
                    explicit_urls=explicit_urls,
                    observations=observations,
                )
                action_plan = await self._ask_model_for_action(
                    request=request,
                    state=state,
                    step_index=step_index,
                    max_steps=max_steps,
                )
                action_name = str(action_plan.get("action") or "").strip().lower()
                if action_name == "finish":
                    final_answer = str(action_plan.get("answer") or "").strip() or "Web task finished."
                    break

                step_result = await self._run_agent_action(
                    proxy=proxy,
                    action_plan=action_plan,
                    created_targets=created_targets,
                    current_target=current_target,
                )
                if step_result.get("current_target"):
                    current_target = str(step_result["current_target"])
                observation = step_result.get("observation")
                if observation:
                    observations.append(observation)
            else:
                final_answer = "未能在限制步骤内完成浏览任务。请缩小范围，或直接提供更明确的网址。"

            payload = {
                "message": final_answer,
                "request": request,
                "explicit_urls": explicit_urls,
                "managed_targets": created_targets,
                "observations": observations[-6:],
            }
            return ExecutionResult.success(message=final_answer, data=payload)
        except ToolError as exc:
            return ExecutionResult.from_error(str(exc), exc.error_type)
        except httpx.HTTPError as exc:
            logger.error("browser_cdp agent HTTP failure: %s", exc)
            return ExecutionResult.from_error(str(exc), ErrorType.TOOL_ERROR)
        finally:
            await self._close_created_targets(proxy, created_targets)

    async def _build_agent_state(
        self,
        *,
        proxy: _BrowserProxyClient,
        created_targets: List[str],
        current_target: Optional[str],
        request: str,
        explicit_urls: List[str],
        observations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Collect the bounded browser state given to the local model."""

        all_targets = await proxy.targets()
        managed_targets = []
        for item in all_targets:
            target_id = str(item.get("targetId") or "").strip()
            if target_id and target_id in created_targets:
                managed_targets.append(
                    {
                        "targetId": target_id,
                        "title": str(item.get("title") or "").strip(),
                        "url": str(item.get("url") or "").strip(),
                    }
                )

        current_info: Dict[str, Any] = {}
        site_urls = list(explicit_urls)
        if current_target and current_target in created_targets:
            try:
                current_info = await proxy.info(current_target)
            except httpx.HTTPError:
                current_info = {}
            current_url = str(current_info.get("url") or "").strip()
            if current_url and current_url not in site_urls:
                site_urls.append(current_url)

        return {
            "request": request,
            "current_target": current_target,
            "current_info": current_info,
            "managed_targets": managed_targets,
            "explicit_urls": explicit_urls,
            "site_hints": self._load_site_hints(site_urls),
            "recent_observations": observations[-4:],
        }

    async def _ask_model_for_action(
        self,
        *,
        request: str,
        state: Dict[str, Any],
        step_index: int,
        max_steps: int,
    ) -> Dict[str, Any]:
        """Prompt the local model for the next browser action."""

        provider = get_llm_provider()
        prompt = self._build_agent_prompt(
            request=request,
            state=state,
            step_index=step_index,
            max_steps=max_steps,
        )
        retry_prompt = f"{prompt}\n\nThe previous response was not valid JSON. Return one JSON object only."

        for candidate in (prompt, retry_prompt):
            response = await provider.generate(candidate, max_tokens=320, temperature=0.0)
            parsed = self._parse_json_object(response.content)
            if parsed is not None:
                return parsed

        raise ToolError("Local model did not return valid JSON for web-access planning.", ErrorType.PARSE_ERROR)

    def _build_agent_prompt(
        self,
        *,
        request: str,
        state: Dict[str, Any],
        step_index: int,
        max_steps: int,
    ) -> str:
        """Build the action-planning prompt."""

        state_json = json.dumps(state, ensure_ascii=False, indent=2)
        return f"""Return JSON only.
You are the LocalClaw web-access controller.

Goal:
- Finish the user's real web task with the minimum necessary actions.
- Be evidence-driven: inspect results, then adapt.
- Prefer low-friction reads first. Use HTTP/Jina when a simple page read is enough.
- Use Chrome CDP when the task needs login state, dynamic rendering, search engine navigation, page interaction, or anti-bot-sensitive access.
- Only use tabs listed in managed_targets. Never touch the user's unrelated tabs.
- This runtime is text-centric. Prefer DOM extraction and page text over screenshots.
- Stop as soon as you have enough evidence to answer.

Available actions:
- {{"action":"finish","answer":"final reply in the user's language"}}
- {{"action":"fetch","url":"https://example.com","mode":"http"}}
- {{"action":"fetch","url":"https://example.com","mode":"jina"}}
- {{"action":"new_tab","url":"https://example.com"}}
- {{"action":"info","target":"TARGET_ID"}}
- {{"action":"navigate","target":"TARGET_ID","url":"https://example.com"}}
- {{"action":"back","target":"TARGET_ID"}}
- {{"action":"eval","target":"TARGET_ID","script":"(() => {{ ... }})()"}}
- {{"action":"click","target":"TARGET_ID","selector":"css selector"}}
- {{"action":"click_at","target":"TARGET_ID","selector":"css selector"}}
- {{"action":"scroll","target":"TARGET_ID","direction":"bottom"}}
- {{"action":"close","target":"TARGET_ID"}}

Rules:
- Use complete URLs, not placeholders.
- Use short, serializable eval results.
- If the request cannot proceed until the user logs into Chrome or opens a page manually, finish with a concise instruction.
- You are currently on step {step_index} of {max_steps}.

User request:
{json.dumps(request, ensure_ascii=False)}

Current state:
{state_json}
"""

    async def _run_agent_action(
        self,
        *,
        proxy: _BrowserProxyClient,
        action_plan: Dict[str, Any],
        created_targets: List[str],
        current_target: Optional[str],
    ) -> Dict[str, Any]:
        """Execute the model-selected browser action."""

        action = str(action_plan.get("action") or "").strip().lower()

        if action == "fetch":
            url = str(action_plan.get("url") or "").strip()
            mode = str(action_plan.get("mode") or "http").strip().lower()
            self._require_value(url, "url")
            payload = await self._fetch_url(url, mode)
            return {
                "observation": {"kind": "fetch", "mode": mode, "url": url, "details": payload},
                "current_target": current_target,
            }

        if action == "new_tab":
            url = str(action_plan.get("url") or "").strip()
            self._require_value(url, "url")
            payload = await proxy.new_tab(url)
            target_id = str(payload.get("targetId") or "").strip()
            if not target_id:
                raise ToolError("CDP proxy did not return a targetId for new_tab.", ErrorType.TOOL_ERROR)
            created_targets.append(target_id)
            info = await proxy.info(target_id)
            return {
                "observation": {"kind": "new_tab", "target": target_id, "url": url, "details": info},
                "current_target": target_id,
            }

        target = str(action_plan.get("target") or current_target or "").strip()
        self._require_value(target, "target")
        if target not in created_targets:
            raise ToolError(f"Refusing to operate on unmanaged target '{target}'.", ErrorType.PERMISSION_ERROR)

        if action == "info":
            details = await proxy.info(target)
        elif action == "navigate":
            next_url = str(action_plan.get("url") or "").strip()
            self._require_value(next_url, "url")
            details = await proxy.navigate(target, next_url)
            details["page"] = await proxy.info(target)
        elif action == "back":
            details = await proxy.back(target)
            details["page"] = await proxy.info(target)
        elif action == "eval":
            raw_script = str(action_plan.get("script") or "").strip()
            self._require_value(raw_script, "script")
            details = await proxy.eval(target, raw_script)
        elif action == "click":
            raw_selector = str(action_plan.get("selector") or "").strip()
            self._require_value(raw_selector, "selector")
            details = await proxy.click(target, raw_selector)
            details["page"] = await proxy.info(target)
        elif action == "click_at":
            raw_selector = str(action_plan.get("selector") or "").strip()
            self._require_value(raw_selector, "selector")
            details = await proxy.click_at(target, raw_selector)
            details["page"] = await proxy.info(target)
        elif action == "scroll":
            details = await proxy.scroll(
                target,
                direction=str(action_plan.get("direction") or "down"),
                y=action_plan.get("y"),
            )
            details["page"] = await proxy.info(target)
        elif action == "close":
            details = await proxy.close(target)
            if target in created_targets:
                created_targets.remove(target)
            next_target = created_targets[-1] if created_targets else None
            return {
                "observation": {"kind": "close", "target": target, "details": details},
                "current_target": next_target,
            }
        else:
            raise ToolError(f"Unsupported browser agent action: {action}", ErrorType.VALIDATION_ERROR)

        return {
            "observation": {"kind": action, "target": target, "details": details},
            "current_target": target,
        }

    async def _fetch_url(self, url: str, mode: str) -> Dict[str, Any]:
        """Fetch a URL through plain HTTP or Jina."""

        normalized_mode = mode if mode in {"http", "jina"} else "http"
        fetch_url = url
        if normalized_mode == "jina":
            parsed = urlparse(url)
            if not parsed.scheme:
                raise ToolError("Jina fetch requires an absolute URL.", ErrorType.VALIDATION_ERROR)
            fetch_url = f"https://r.jina.ai/http://{parsed.netloc}{parsed.path or ''}"
            if parsed.query:
                fetch_url += f"?{parsed.query}"

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(fetch_url)
            response.raise_for_status()
            body = response.text

        return {
            "mode": normalized_mode,
            "url": url,
            "status_code": response.status_code,
            "body": self._truncate_text(body, self.MAX_FETCH_CHARS),
        }

    async def _ensure_proxy_ready(self, *, start_proxy: bool) -> Dict[str, Any]:
        """Ensure the upstream proxy is running and connected to Chrome."""

        initial_health = await self._safe_health()
        if initial_health.get("ok") and initial_health.get("connected"):
            return {"ok": True, "connected": True, "started_proxy": False, "message": "Chrome CDP proxy is ready."}

        if initial_health.get("ok") and not initial_health.get("connected"):
            return {"ok": True, "connected": False, "started_proxy": False, "message": self._chrome_debugging_help()}

        if not start_proxy:
            return {"ok": False, "connected": False, "started_proxy": False, "message": "Chrome CDP proxy is not running."}

        proxy_script = self._resolve_proxy_script_path()
        if proxy_script is None:
            return {
                "ok": False,
                "connected": False,
                "started_proxy": False,
                "message": "web-access is installed without scripts/cdp-proxy.mjs, so the CDP proxy cannot start.",
            }

        node_path = shutil.which("node")
        if not node_path:
            return {
                "ok": False,
                "connected": False,
                "started_proxy": False,
                "message": "Node.js was not found. Install Node.js 22+ to use web-access.",
            }

        node_major = self._node_major_version(node_path)
        if node_major is not None and node_major < 22:
            return {
                "ok": False,
                "connected": False,
                "started_proxy": False,
                "message": f"Node.js {node_major} is too old for web-access. Install Node.js 22+.",
            }

        self._spawn_proxy_process(node_path, proxy_script)

        for _ in range(10):
            await self._sleep_short()
            health = await self._safe_health()
            if health.get("ok") and health.get("connected"):
                return {"ok": True, "connected": True, "started_proxy": True, "message": "Chrome CDP proxy started and connected."}
            if health.get("ok"):
                return {"ok": True, "connected": False, "started_proxy": True, "message": self._chrome_debugging_help()}

        return {
            "ok": False,
            "connected": False,
            "started_proxy": True,
            "message": "CDP proxy did start, but Chrome is still unavailable. " + self._chrome_debugging_help(),
        }

    async def _safe_health(self) -> Dict[str, Any]:
        """Read proxy health without raising transport exceptions."""

        try:
            health = await _BrowserProxyClient().health()
        except httpx.HTTPError:
            return {"ok": False, "connected": False}
        return {"ok": bool(health.get("ok")), "connected": bool(health.get("connected")), "raw": health}

    def _resolve_proxy_script_path(self) -> Optional[Path]:
        """Find the adapted upstream cdp-proxy.mjs shipped with web-access."""

        settings = get_settings()
        candidates: List[Path] = []
        for base in list(reversed(settings.get_skill_search_paths())) + [settings.bundled_skill_catalog_dir]:
            candidate = base / "web-access" / "scripts" / "cdp-proxy.mjs"
            if candidate not in candidates:
                candidates.append(candidate)

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _spawn_proxy_process(self, node_path: str, proxy_script: Path) -> None:
        """Start the Node proxy in the background."""

        settings = get_settings()
        log_path = settings.data_dir / "web-access-cdp-proxy.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")

        kwargs: Dict[str, Any] = {
            "stdout": log_file,
            "stderr": log_file,
            "stdin": subprocess.DEVNULL,
            "cwd": str(proxy_script.parent),
            "start_new_session": True,
        }

        if sys.platform == "win32":
            creationflags = 0
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            kwargs["creationflags"] = creationflags

        subprocess.Popen([node_path, str(proxy_script)], **kwargs)

    def _node_major_version(self, node_path: str) -> Optional[int]:
        """Return the installed Node.js major version."""

        try:
            completed = subprocess.run(
                [node_path, "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
        except Exception:
            return None

        version = completed.stdout.strip() or completed.stderr.strip()
        match = re.match(r"v?(\d+)", version)
        if not match:
            return None
        return int(match.group(1))

    def _chrome_debugging_help(self) -> str:
        """Return the machine-specific help text for enabling Chrome debugging."""

        return (
            "Chrome 远程调试尚未连接。请在 Chrome 打开 "
            "`chrome://inspect/#remote-debugging`，启用 "
            "`Allow remote debugging for this browser instance`，"
            "必要时重启 Chrome，然后重试。"
        )

    def _load_site_hints(self, urls: List[str]) -> List[Dict[str, str]]:
        """Load any bundled site-pattern notes for the current domains."""

        hints: List[Dict[str, str]] = []
        seen: set[str] = set()
        skill_root = self._resolve_web_access_skill_root()
        if skill_root is None:
            return hints

        site_dir = skill_root / "references" / "site-patterns"
        if not site_dir.exists():
            return hints

        for url in urls:
            hostname = self._hostname_from_url(url)
            if not hostname:
                continue
            for candidate_name in self._candidate_site_pattern_names(hostname):
                path = site_dir / f"{candidate_name}.md"
                if not path.exists():
                    continue
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                try:
                    text = path.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                hints.append({"domain": hostname, "path": str(path), "content": self._truncate_text(text, 1200)})
                break

        return hints

    def _resolve_web_access_skill_root(self) -> Optional[Path]:
        """Resolve the best web-access skill directory for runtime references."""

        settings = get_settings()
        candidates: List[Path] = []
        for base in list(reversed(settings.get_skill_search_paths())) + [settings.bundled_skill_catalog_dir]:
            candidate = base / "web-access"
            if candidate not in candidates:
                candidates.append(candidate)

        for candidate in candidates:
            if (candidate / "SKILL.md").exists():
                return candidate
        return None

    def _candidate_site_pattern_names(self, hostname: str) -> List[str]:
        """Generate candidate filenames for a hostname."""

        candidates = [hostname]
        if hostname.startswith("www."):
            candidates.append(hostname[4:])
        parts = hostname.split(".")
        if len(parts) > 2:
            candidates.append(".".join(parts[-2:]))
        return [candidate for candidate in candidates if candidate]

    def _hostname_from_url(self, url: str) -> str:
        """Return a normalized hostname from a URL."""

        try:
            parsed = urlparse(url)
        except Exception:
            return ""
        return str(parsed.netloc or "").strip().lower()

    async def _close_created_targets(self, proxy: _BrowserProxyClient, created_targets: List[str]) -> None:
        """Close any tabs the tool created."""

        for target in list(reversed(created_targets)):
            try:
                await proxy.close(target)
            except Exception:
                logger.debug("Failed to close managed target %s", target, exc_info=True)

    def _parse_json_object(self, content: str) -> Optional[Dict[str, Any]]:
        """Best-effort JSON object parsing."""

        text = content.strip()
        if text.startswith("```json") and "```" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif text.startswith("```") and "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            text = text[start:end]

        try:
            payload = json.loads(text)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _truncate_text(self, value: Any, limit: int) -> str:
        """Render and truncate arbitrary values for model context."""

        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value)
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _require_value(self, value: Optional[str], field_name: str) -> None:
        """Raise a validation error when a required action field is blank."""

        if not str(value or "").strip():
            raise ToolError(f"Missing required parameter: {field_name}", ErrorType.VALIDATION_ERROR)

    async def _sleep_short(self) -> None:
        """Bounded wait used while polling proxy readiness."""

        import asyncio

        await asyncio.sleep(1.0)


def register_browser_cdp_tools() -> None:
    """Register the browser CDP tool."""

    register_tool(BrowserCDPTool())
