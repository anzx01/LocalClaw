"""OpenClaw-style model-first runtime for LocalClaw."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from localclaw.core.json_utils import extract_last_json_object
from localclaw.core.models import AgentDecision, AgentDecisionMode, Context, Message
from localclaw.llm.provider import get_llm_provider
from localclaw.skills.registry.registry import SkillRegistry
from localclaw.tools.base import ToolRegistry


logger = logging.getLogger(__name__)


NEWS_FEED_LOCALE = "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"


class OpenClawRuntime:
    """Ask the local model to answer directly or pick a skill/tool."""

    def __init__(
        self,
        skill_registry: SkillRegistry,
        tool_registry: ToolRegistry,
        refine_skill_decision: bool = True,
        enable_request_guardrails: bool = True,
    ) -> None:
        self._skill_registry = skill_registry
        self._tool_registry = tool_registry
        self._enable_skill_refinement = refine_skill_decision
        self._enable_request_guardrails = enable_request_guardrails

    def _guardrails_enabled(self) -> bool:
        """Return whether deterministic guardrails are enabled."""

        return bool(getattr(self, "_enable_request_guardrails", True))

    def _skill_refinement_enabled(self) -> bool:
        """Return whether second-pass skill refinement is enabled."""

        return bool(getattr(self, "_enable_skill_refinement", True))

    def _get_skill_registry(self) -> Optional[SkillRegistry]:
        """Return the configured skill registry, if available."""

        return getattr(self, "_skill_registry", None)

    def _get_tool_registry(self) -> Optional[ToolRegistry]:
        """Return the configured tool registry, if available."""

        return getattr(self, "_tool_registry", None)

    def _lookup_tool(self, tool_name: str) -> Any:
        """Best-effort tool lookup that tolerates runtime test doubles."""

        registry = self._get_tool_registry()
        if registry is None:
            return None
        try:
            return registry.get(tool_name)
        except Exception:
            return None

    async def decide(self, message: Message, context: Optional[Context] = None) -> AgentDecision:
        """Return the model's handling decision for a user message."""

        del context  # Reserved for future multi-turn skill prompting.

        decision = AgentDecision(
            mode=AgentDecisionMode.UNKNOWN,
            confidence=0.0,
            source="openclaw_runtime",
            raw_message=message.content,
        )
        if self._guardrails_enabled():
            fast_intent = self._build_basic_intent_guardrail(str(message.content or "").strip())
            if fast_intent:
                return AgentDecision(
                    mode=AgentDecisionMode.INTENT,
                    intent_name=fast_intent,
                    params={},
                    confidence=0.99,
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="basic_intent_guardrail",
                )

            # Fast-path: detect file-read/summarize patterns before calling the LLM.
            # This avoids a slow or timing-out model call for deterministic filesystem requests.
            fast_skill_decision = self._build_fast_skill_guardrail(str(message.content or "").strip(), message)
            if fast_skill_decision is not None:
                return fast_skill_decision

        llm_provider = get_llm_provider()
        if not await llm_provider.is_available():
            return decision

        prompts = [
            (self._build_decision_prompt(message), 384),
            (self._build_retry_prompt(message), 256),
        ]
        for prompt, max_tokens in prompts:
            try:
                response = await llm_provider.generate(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
            except Exception as exc:
                logger.debug("OpenClaw runtime prompt failed: %s", exc)
                continue

            decision = self._decision_from_model_output(response.content, message.content)
            if decision.mode != AgentDecisionMode.UNKNOWN:
                break

        if self._guardrails_enabled():
            decision = self._apply_request_guardrails(decision, message)

        if decision.mode == AgentDecisionMode.SKILL and decision.source == "openclaw_runtime_guardrail":
            return decision

        if decision.mode == AgentDecisionMode.SKILL and self._skill_refinement_enabled():
            refined = await self._refine_skill_decision(decision, message)
            if refined.mode != AgentDecisionMode.UNKNOWN:
                return refined

        return decision

    async def fallback_to_chat_answer(
        self,
        message: Message,
        context: Optional[Context] = None,
    ) -> AgentDecision:
        """Ask the local model to answer conversationally when no action was planned."""

        del context  # Reserved for future multi-turn prompting.

        if self._guardrails_enabled():
            web_fallback = self._build_unknown_web_fallback_decision(message.content)
            if web_fallback is not None:
                return web_fallback

        llm_provider = get_llm_provider()
        if not await llm_provider.is_available():
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime_chat_fallback",
                raw_message=message.content,
            )

        system_prompt = (
            "You are LocalClaw's local-model conversational fallback. "
            "The runtime could not map the user's request to an installed skill, tool, or built-in action. "
            "Reply naturally in the user's language. "
            "Do not claim that you executed commands, accessed files, or fetched live/current data. "
            "If the request depends on current weather, current external information, filesystem state, system state, "
            "or the physical world and you cannot verify it here, say that plainly and briefly."
        )

        response_content = ""
        try:
            response = await llm_provider.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message.content},
                ],
                max_tokens=512,
                temperature=0.3,
            )
            response_content = response.content
        except Exception as exc:
            logger.debug("OpenClaw runtime chat fallback failed: %s", exc)

        if not response_content.strip():
            try:
                response = await llm_provider.generate(
                    prompt=f"User: {json.dumps(message.content, ensure_ascii=False)}",
                    system_prompt=system_prompt,
                    max_tokens=512,
                    temperature=0.3,
                )
                response_content = response.content
            except Exception as exc:
                logger.debug("OpenClaw runtime generate fallback failed: %s", exc)

        decision = self._decision_from_model_output(response_content, message.content)
        if decision.mode == AgentDecisionMode.ANSWER and decision.answer:
            decision.source = "openclaw_runtime_chat_fallback"
            decision.confidence = max(decision.confidence, 0.5)
            return decision

        return AgentDecision(
            mode=AgentDecisionMode.UNKNOWN,
            confidence=0.0,
            source="openclaw_runtime_chat_fallback",
            raw_message=message.content,
        )

    def _build_decision_prompt(self, message: Message) -> str:
        """Build the primary model-first decision prompt."""

        today = datetime.now().strftime("%Y-%m-%d")
        user_request = json.dumps(message.content, ensure_ascii=False)
        if not self._guardrails_enabled():
            installed_skills = self._build_compact_skill_name_list()
            installed_tools = self._build_compact_tool_name_list()
            return f"""Return JSON only.
You are the LocalClaw local-model runtime.
Current local date: {today}

Output exactly one JSON object in one of these forms:
- {{"mode":"answer","answer":"..."}}
- {{"mode":"skill","skill":"<exact installed skill name>","params":{{...}}}}
- {{"mode":"tool","tool":"<exact tool name>","params":{{...}}}}
- {{"mode":"intent","intent":"<built-in intent>","params":{{...}}}}
- If unsure, return {{"mode":"unknown"}}.

Rules:
- Use mode="answer" only for casual chat, explanations, writing, or stable general knowledge.
- If current date/time/weekday, current weather, live web data, files, system state, or tool use is needed, do not answer from memory.
- Prefer mode="intent" for common built-in actions like help, greeting, status, list_skills, date_query, time_now, check_weather, list_folders, file_list, and check_disk_space.
- For current weekday/date/time questions, prefer intent "date_query".
- For current weather or forecast questions, prefer intent "check_weather".
- Prefer mode="skill" when the user explicitly asks for an installed skill or uses "/<skill> ...".
- Prefer mode="tool" only for explicit tool-style requests such as "/cmd <command>" and "/shell <command>".
- For "/cmd <command>", choose tool "safe_shell" with params.command.
- For "/shell <command>", choose tool "shell" with params.command.
- Use exact installed skill names and tool names from the lists below.

Installed skills: {installed_skills}
Installed tools: {installed_tools}

Examples:
- Current weekday/date/time question -> {{"mode":"intent","intent":"date_query","params":{{}}}}
- Current weather question -> {{"mode":"intent","intent":"check_weather","params":{{}}}}
- /cmd git status -> {{"mode":"tool","tool":"safe_shell","params":{{"command":"git status"}}}}

User: {user_request}
"""
        return f"""Return JSON only.
You are the LocalClaw local-model runtime.
Current local date: {today}

First decide whether you can answer the user directly.
- Use mode="answer" only for normal conversation, greetings, help/capability questions, writing, explanations, or stable general knowledge.
- If the request depends on current weather, current external data, files in the workspace, shell commands, HTTP requests, or installed skill instructions, do not answer from memory. Choose a skill or tool instead.
- Requests for today's news, latest headlines, current events, latest updates, live prices, live scores, or recent web information require a skill or tool, not answer/help.
- Prefer a skill over a raw tool when one skill clearly fits.
- If you choose a skill, use the skill name exactly as listed in <available_skills>.
- If you choose a tool, use the tool name exactly as listed in <available_tools>.
- For "/cmd <command>", choose tool "safe_shell" with params.command.
- For "/shell <command>", choose tool "shell" with params.command.
- For "/<skill> ...", choose that skill directly.
- For weather questions like "今天热吗？", "明天北京天气怎么样", "上海气温", do not answer from memory.
  Prefer a weather-related installed skill (for example skill.weather / skill.weather.forecast).
  If no weather skill is available, choose intent "check_weather" with best-effort params:
  day_offset: 0 for 今天, 1 for 明天, 2 for 后天; day_label matches that day.
- For requests like "看看我桌面有哪些文件夹", choose intent "list_folders" with params.path="~/Desktop" and params.folders_only=true.
- For requests like "列出桌面文件" or "查看 Desktop", choose tool "file_list" or intent "file_list" with params.path="~/Desktop".
- For requests like "我D盘有哪些目录" or "查看 D 盘文件夹", choose intent "list_folders" with params.path="D:/" and params.folders_only=true.
- For requests like "列出 D 盘文件", choose tool "file_list" or intent "file_list" with params.path="D:/".
- For requests like "我C盘空间还剩多少" or "D盘还有多少可用空间", choose intent "check_disk_space" with params.path="C:/" or "D:/".
- Reply in the user's language.

Output one JSON object with exactly one mode:
- {{"mode":"answer","answer":"..."}}
- {{"mode":"skill","skill":"repo.fs","params":{{...}}}}
- {{"mode":"tool","tool":"safe_shell","params":{{...}}}}
- {{"mode":"intent","intent":"help","params":{{...}}}}

<available_skills>
{self._build_skill_catalog(compact=True)}
</available_skills>

<available_tools>
{self._build_tool_catalog(compact=True)}
</available_tools>

User: {user_request}
"""

    def _build_retry_prompt(self, message: Message) -> str:
        """Build a shorter retry prompt for smaller local models."""

        user_request = json.dumps(message.content, ensure_ascii=False)
        if not self._guardrails_enabled():
            installed_skills = self._build_compact_skill_name_list()
            installed_tools = self._build_compact_tool_name_list()
            return f"""Return JSON only.
Output one JSON object only:
- {{"mode":"answer","answer":"..."}}
- {{"mode":"skill","skill":"<exact installed skill name>","params":{{...}}}}
- {{"mode":"tool","tool":"<exact tool name>","params":{{...}}}}
- {{"mode":"intent","intent":"date_query","params":{{}}}}
- {{"mode":"intent","intent":"check_weather","params":{{}}}}
- {{"mode":"unknown"}}

Rules:
- Use answer only for casual chat or stable knowledge.
- Current/live/date/time/weather/files/system requests must not use answer.
- Current weekday/date/time -> intent "date_query".
- Current weather/forecast -> intent "check_weather".
- /cmd <command> -> tool "safe_shell".
- /shell <command> -> tool "shell".
- Use exact installed skill/tool names when choosing skill/tool.

Installed skills: {installed_skills}
Installed tools: {installed_tools}

User: {user_request}
"""
        return f"""Return JSON only.
Choose one mode for the LocalClaw user request:
- answer: chat/help/general knowledge only
- skill: if files/weather/web/installed skill is needed
- tool: for /cmd or /shell

Never answer weather, news, latest/current web information, filesystem, network, or shell requests from memory.
Use listed skill names and tool names exactly.
Weather requests like "今天热吗" or "明天北京天气" should resolve to a weather skill when available,
otherwise intent "check_weather" (with day_offset/day_label).
Desktop folder requests like "看看我桌面有哪些文件夹" should resolve to intent "list_folders" with path "~/Desktop".
Desktop file listing requests like "列出桌面文件" should resolve to tool or intent "file_list" with path "~/Desktop".
Drive folder requests like "我D盘有哪些目录" should resolve to intent "list_folders" with path "D:/".
Drive file listing requests like "列出 D 盘文件" should resolve to tool or intent "file_list" with path "D:/".
Drive free-space requests like "我C盘空间还剩多少" should resolve to intent "check_disk_space" with path "C:/".

<available_skills>
{self._build_skill_catalog(compact=True)}
</available_skills>

<available_tools>
{self._build_tool_catalog(compact=True)}
</available_tools>

User: {user_request}
"""

    def _build_skill_catalog(self, compact: bool = False) -> str:
        """Serialize model-invocable skills in an OpenClaw-like prompt format."""

        registry = self._get_skill_registry()
        if registry is None:
            return "  <none />"

        try:
            infos = registry.get_model_invocable_info()
        except Exception:
            infos = []

        if not infos:
            return "  <none />"

        lines = []
        for info in infos:
            metadata = info.get("metadata", {}) or {}
            source_path = str(metadata.get("source_path", "")).strip() or "(built-in)"
            input_names = ", ".join((info.get("inputs") or {}).keys()) or "none"
            aliases = ", ".join(info.get("aliases") or []) or "none"
            lines.append("  <skill>")
            lines.append(f"    <name>{self._xml_escape(str(info.get('skill_key') or info.get('name') or 'unknown'))}</name>")
            lines.append(f"    <canonical_name>{self._xml_escape(str(info.get('name') or 'unknown'))}</canonical_name>")
            if not compact:
                lines.append(f"    <description>{self._xml_escape(str(info.get('description') or '').strip())}</description>")
                lines.append(f"    <inputs>{self._xml_escape(input_names)}</inputs>")
                lines.append(f"    <aliases>{self._xml_escape(aliases)}</aliases>")
            lines.append(f"    <location>{self._xml_escape(source_path)}</location>")
            lines.append("  </skill>")
        return "\n".join(lines)

    def _build_tool_catalog(self, compact: bool = False) -> str:
        """Serialize available tools for model selection."""

        registry = self._get_tool_registry()
        if registry is None:
            return "  <none />"

        try:
            infos = sorted(
                registry.get_all_info(),
                key=lambda item: str(item.get("name", "")),
            )
        except Exception:
            infos = []
        if not infos:
            return "  <none />"

        lines = []
        for info in infos:
            tool_name = str(info.get("name", "unknown"))
            if tool_name.startswith("_"):
                continue
            lines.append("  <tool>")
            lines.append(f"    <name>{self._xml_escape(tool_name)}</name>")
            if not compact:
                lines.append(f"    <description>{self._xml_escape(str(info.get('description', '')).strip())}</description>")
                lines.append(
                    f"    <inputs>{self._xml_escape(', '.join((info.get('inputs') or {}).keys()) or 'none')}</inputs>"
                )
                lines.append(f"    <risk>{self._xml_escape(str(info.get('risk_level', 'low')))}</risk>")
            lines.append("  </tool>")
        return "\n".join(lines) if lines else "  <none />"

    def _build_compact_skill_name_list(self) -> str:
        """Return a compact, token-light list of model-invocable skill names."""

        registry = self._get_skill_registry()
        if registry is None:
            return "<none>"

        try:
            infos = registry.get_model_invocable_info()
        except Exception:
            infos = []

        names: list[str] = []
        seen: set[str] = set()
        for info in infos:
            name = str(info.get("name") or info.get("skill_key") or "").strip()
            lowered = name.lower()
            if not name or lowered in seen:
                continue
            seen.add(lowered)
            names.append(name)
        return ", ".join(names) if names else "<none>"

    def _build_compact_tool_name_list(self) -> str:
        """Return a compact, token-light list of available tool names."""

        registry = self._get_tool_registry()
        if registry is None:
            return "<none>"

        try:
            infos = registry.get_all_info()
        except Exception:
            infos = []

        names: list[str] = []
        for info in infos:
            tool_name = str(info.get("name", "")).strip()
            if not tool_name or tool_name.startswith("_"):
                continue
            names.append(tool_name)
        return ", ".join(sorted(set(names))) if names else "<none>"

    async def _refine_skill_decision(self, decision: AgentDecision, message: Message) -> AgentDecision:
        """Load the selected skill's docs and ask the model to confirm params."""

        registry = self._get_skill_registry()
        if registry is None:
            return decision

        skill_identifier = decision.skill_name or ""
        skill = registry.get(skill_identifier)
        if skill is None:
            return decision

        llm_provider = get_llm_provider()
        if self.is_instruction_skill(skill_identifier):
            refined = await self._refine_instruction_skill_decision(
                skill_identifier=skill_identifier,
                message=message,
                initial_params=decision.params or {},
            )
            if refined.mode != AgentDecisionMode.UNKNOWN:
                return refined
            return decision

        skill_doc = self._build_skill_detail(skill_identifier)
        user_request = json.dumps(message.content, ensure_ascii=False)
        params_snapshot = json.dumps(decision.params or {}, ensure_ascii=False)
        prompt = f"""Return JSON only.
You already selected a LocalClaw skill. Read the selected skill details and return the best executable JSON.

Rules:
- Usually return {{"mode":"skill","skill":"{self._json_escape(skill_identifier)}","params":{{...}}}}
- Only return mode="answer" if the user can be answered directly without running the skill
- Use only declared skill inputs when building params
- Do not invent missing file paths, URLs, or secrets
- Keep the same reply language as the user

<selected_skill>
{skill_doc}
</selected_skill>

Initial params guess: {params_snapshot}
User: {user_request}
"""

        try:
            response = await llm_provider.generate(
                prompt,
                max_tokens=320,
                temperature=0.0,
            )
        except Exception as exc:
            logger.debug("Skill refinement prompt failed for %s: %s", skill_identifier, exc)
            return decision

        refined = self._decision_from_model_output(response.content, message.content)
        if refined.mode == AgentDecisionMode.UNKNOWN:
            return decision
        if refined.mode == AgentDecisionMode.SKILL and not refined.skill_name:
            refined.skill_name = skill_identifier
        return refined

    def is_instruction_skill(self, skill_identifier: str) -> bool:
        """Return True when a skill relies on SKILL.md guidance instead of declarative actions."""

        registry = self._get_skill_registry()
        if registry is None:
            return False

        skill = registry.get(skill_identifier)
        if skill is None:
            return False

        definition = skill.get_definition() if hasattr(skill, "get_definition") else None
        if definition is None:
            return False

        actions = list(getattr(definition, "actions", []) or [])
        if actions:
            return False

        metadata = getattr(definition, "metadata", {}) or {}
        source_path = str(metadata.get("source_path", "")).strip() or "(built-in)"
        documentation = self._load_skill_documentation(source_path, metadata)
        return bool(str(documentation).strip())

    async def decide_instruction_skill_next_action(
        self,
        skill_identifier: str,
        message: Message,
        context: Optional[Context] = None,
        observations: Optional[List[Dict[str, Any]]] = None,
    ) -> AgentDecision:
        """Ask the local model for the next executable action for an instruction-only skill."""

        if not self.is_instruction_skill(skill_identifier):
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime_instruction_skill",
                raw_message=message.content,
            )

        llm_provider = get_llm_provider()
        if not await llm_provider.is_available():
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime_instruction_skill",
                raw_message=message.content,
            )

        prompt = self._build_instruction_skill_action_prompt(
            skill_identifier=skill_identifier,
            message=message,
            context=context,
            observations=observations or [],
        )
        try:
            response = await llm_provider.generate(
                prompt,
                max_tokens=384,
                temperature=0.0,
            )
        except Exception as exc:
            logger.debug("Instruction skill prompt failed for %s: %s", skill_identifier, exc)
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime_instruction_skill",
                raw_message=message.content,
            )

        decision = self._decision_from_model_output(response.content, message.content)
        if decision.mode == AgentDecisionMode.SKILL and decision.skill_name == skill_identifier:
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=decision.confidence,
                source="openclaw_runtime_instruction_skill",
                raw_message=message.content,
                rationale="instruction_skill_reselected_itself",
            )

        decision.source = "openclaw_runtime_instruction_skill"
        decision.raw_message = message.content
        return decision

    async def _refine_instruction_skill_decision(
        self,
        *,
        skill_identifier: str,
        message: Message,
        initial_params: Dict[str, Any],
    ) -> AgentDecision:
        """Second-pass refinement for skills that are driven mainly by SKILL.md instructions."""

        llm_provider = get_llm_provider()
        skill_doc = self._build_skill_detail(skill_identifier)
        user_request = json.dumps(message.content, ensure_ascii=False)
        params_snapshot = json.dumps(initial_params or {}, ensure_ascii=False)
        prompt = f"""Return JSON only.
You already selected a LocalClaw instruction skill. Read the selected skill details and return the best executable JSON.

Allowed outputs:
- {{"mode":"skill","skill":"{self._json_escape(skill_identifier)}","params":{{...}}}}
- {{"mode":"tool","tool":"<exact available tool name>","params":{{...}}}}
- {{"mode":"intent","intent":"<built-in intent>","params":{{...}}}}
- {{"mode":"answer","answer":"..."}}

Rules:
- Prefer mode="skill" when the skill will likely need follow-up after seeing tool results.
- You may return mode="tool" or mode="intent" only when one concrete next action is clearly enough.
- If you return mode="skill", keep skill="{self._json_escape(skill_identifier)}".
- Use exact tool names from <available_tools>.
- Do not invent missing file paths, URLs, browser target ids, or secrets.
- Keep the same reply language as the user.

<selected_skill>
{skill_doc}
</selected_skill>

<available_tools>
{self._build_tool_catalog(compact=False)}
</available_tools>

Initial params guess: {params_snapshot}
User: {user_request}
"""

        try:
            response = await llm_provider.generate(
                prompt,
                max_tokens=320,
                temperature=0.0,
            )
        except Exception as exc:
            logger.debug("Instruction skill refinement prompt failed for %s: %s", skill_identifier, exc)
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime",
                raw_message=message.content,
            )

        refined = self._decision_from_model_output(response.content, message.content)
        if refined.mode == AgentDecisionMode.UNKNOWN:
            return refined
        if refined.mode == AgentDecisionMode.SKILL and not refined.skill_name:
            refined.skill_name = skill_identifier
        return refined

    def _build_instruction_skill_action_prompt(
        self,
        *,
        skill_identifier: str,
        message: Message,
        context: Optional[Context],
        observations: List[Dict[str, Any]],
    ) -> str:
        """Build the execution prompt for an instruction-only skill turn."""

        user_request = json.dumps(message.content, ensure_ascii=False)
        context_payload = {
            "inputs": self._trim_prompt_value((context.inputs if context is not None else {})),
            "variables": self._trim_prompt_value((context.variables if context is not None else {})),
        }
        observation_payload = self._trim_prompt_value(observations[-6:])
        return f"""Return JSON only.
You are executing exactly one selected LocalClaw skill.
Read the selected skill and recent observations, then choose the next best executable action.

Allowed outputs:
- {{"mode":"answer","answer":"final reply in the user's language"}}
- {{"mode":"tool","tool":"<exact available tool name>","params":{{...}}}}
- {{"mode":"intent","intent":"<built-in intent>","params":{{...}}}}
- {{"mode":"skill","skill":"<different installed skill name>","params":{{...}}}}

Rules:
- Prefer a concrete tool or built-in intent when action is still needed.
- Do not return the selected skill itself again.
- Use exact names from <available_tools> and <available_skills>.
- Use only information supported by the selected skill instructions and observations.
- Do not invent file paths, URLs, browser target ids, or secrets.
- If the task is complete, return mode="answer" in the user's language.

<selected_skill>
{self._build_skill_detail(skill_identifier)}
</selected_skill>

<available_skills>
{self._build_skill_catalog(compact=False)}
</available_skills>

<available_tools>
{self._build_tool_catalog(compact=False)}
</available_tools>

<task_context>
{self._xml_escape(json.dumps(context_payload, ensure_ascii=False))}
</task_context>

<recent_observations>
{self._xml_escape(json.dumps(observation_payload, ensure_ascii=False))}
</recent_observations>

User: {user_request}
"""

    def _build_skill_detail(self, skill_identifier: str) -> str:
        """Build a prompt block for a selected skill, including SKILL.md docs when available."""

        registry = self._get_skill_registry()
        if registry is None:
            return "  <missing />"

        skill = registry.get(skill_identifier)
        if skill is None:
            return "  <missing />"

        definition = skill.get_definition()
        metadata = definition.metadata or {}
        source_path = str(metadata.get("source_path", "")).strip() or "(built-in)"
        documentation = self._load_skill_documentation(source_path, metadata)
        actions_summary = self._summarize_skill_actions(definition.actions)

        lines = [
            f"  <name>{self._xml_escape(str(metadata.get('skill_key', definition.name)))}</name>",
            f"  <canonical_name>{self._xml_escape(definition.name)}</canonical_name>",
            f"  <description>{self._xml_escape(definition.description)}</description>",
            f"  <location>{self._xml_escape(source_path)}</location>",
            f"  <inputs>{self._xml_escape(', '.join(definition.inputs.keys()) or 'none')}</inputs>",
            f"  <tools>{self._xml_escape(', '.join(definition.tools) or 'none')}</tools>",
            f"  <actions>{self._xml_escape(actions_summary)}</actions>",
        ]
        if documentation:
            lines.append(f"  <documentation>{self._xml_escape(documentation)}</documentation>")
        return "\n".join(lines)

    def _load_skill_documentation(self, source_path: str, metadata: Dict[str, Any]) -> str:
        """Return raw SKILL.md instructions or best-effort documentation text."""

        documentation = str(metadata.get("documentation", "") or "").strip()
        if documentation:
            return documentation

        normalized_source = source_path.strip()
        if not normalized_source or normalized_source == "(built-in)":
            return ""

        path = Path(normalized_source)
        try:
            if path.exists() and path.name.upper() == "SKILL.MD":
                text = path.read_text(encoding="utf-8")
                match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
                if match:
                    return match.group(2).strip()
                return text.strip()
        except Exception as exc:
            logger.debug("Failed to load skill documentation from %s: %s", source_path, exc)
        return ""

    def _summarize_skill_actions(self, actions: list[Any]) -> str:
        """Build a compact textual summary of declarative skill actions."""

        if not actions:
            return "none"

        parts = []
        for action in actions[:8]:
            action_type = getattr(action, "type", "transform")
            if action_type == "tool_call":
                parts.append(f"tool:{getattr(action, 'tool', '') or getattr(action, 'name', 'unknown')}")
            elif action_type == "skill_call":
                parts.append(f"skill:{getattr(action, 'skill', '') or getattr(action, 'name', 'unknown')}")
            elif action_type == "condition":
                parts.append(f"condition:{getattr(action, 'condition', '')}")
            else:
                parts.append(str(action_type))
        return "; ".join(parts)

    def _trim_prompt_value(
        self,
        value: Any,
        *,
        max_string: int = 800,
        max_items: int = 12,
    ) -> Any:
        """Trim nested values before placing them into an LLM prompt."""

        if isinstance(value, str):
            compact = re.sub(r"\s+", " ", value).strip()
            if len(compact) <= max_string:
                return compact
            return compact[: max_string - 3].rstrip() + "..."
        if isinstance(value, dict):
            trimmed: Dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= max_items:
                    trimmed["..."] = f"+{len(value) - max_items} more fields"
                    break
                trimmed[str(key)] = self._trim_prompt_value(
                    item,
                    max_string=max_string,
                    max_items=max_items,
                )
            return trimmed
        if isinstance(value, list):
            trimmed_list = [
                self._trim_prompt_value(item, max_string=max_string, max_items=max_items)
                for item in value[:max_items]
            ]
            if len(value) > max_items:
                trimmed_list.append(f"... +{len(value) - max_items} more items")
            return trimmed_list
        return value

    def _decision_from_model_output(self, content: str, raw_message: str) -> AgentDecision:
        """Parse model output into an AgentDecision."""

        normalized = content.strip()
        extracted = self._extract_json_block(normalized)
        if extracted is None:
            if normalized:
                return AgentDecision(
                    mode=AgentDecisionMode.ANSWER,
                    answer=normalized,
                    confidence=0.6,
                    source="openclaw_runtime",
                    raw_message=raw_message,
                )
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime",
                raw_message=raw_message,
            )

        try:
            payload = json.loads(extracted)
        except json.JSONDecodeError:
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime",
                raw_message=raw_message,
            )

        if not isinstance(payload, dict):
            return AgentDecision(
                mode=AgentDecisionMode.UNKNOWN,
                confidence=0.0,
                source="openclaw_runtime",
                raw_message=raw_message,
            )

        params = payload.get("params", {})
        if not isinstance(params, dict):
            params = {}

        raw_mode = str(payload.get("mode", "") or "").strip().lower()
        answer = str(payload.get("answer", "") or "").strip() or None
        rationale = str(payload.get("rationale", "") or "").strip() or None
        confidence = payload.get("confidence", 0.9)
        try:
            parsed_confidence = float(confidence)
        except (TypeError, ValueError):
            parsed_confidence = 0.9
        parsed_confidence = max(0.0, min(parsed_confidence, 1.0))

        raw_skill_name = payload.get("skill") or payload.get("skill_name")
        if raw_mode == "skill":
            raw_skill_name = raw_skill_name or payload.get("name")
        skill_name = self._resolve_skill_name(raw_skill_name)
        tool_name = str(payload.get("tool") or payload.get("tool_name") or "").strip() or None
        intent_name = str(payload.get("intent") or payload.get("intent_name") or "").strip() or None

        mode = AgentDecisionMode.UNKNOWN
        if raw_mode in AgentDecisionMode._value2member_map_:
            mode = AgentDecisionMode(raw_mode)
        elif answer:
            mode = AgentDecisionMode.ANSWER
        elif skill_name:
            mode = AgentDecisionMode.SKILL
        elif tool_name:
            mode = AgentDecisionMode.TOOL
        elif intent_name:
            mode = AgentDecisionMode.INTENT

        if intent_name and intent_name.startswith("skill."):
            resolved = self._resolve_skill_name(intent_name[6:])
            if resolved:
                mode = AgentDecisionMode.SKILL
                skill_name = resolved
                intent_name = None
        elif intent_name and intent_name.startswith("tool."):
            mode = AgentDecisionMode.TOOL
            tool_name = intent_name[5:]
            intent_name = None

        if mode == AgentDecisionMode.SKILL and not skill_name:
            mode = AgentDecisionMode.UNKNOWN
        if mode == AgentDecisionMode.TOOL and not tool_name:
            mode = AgentDecisionMode.UNKNOWN
        if mode == AgentDecisionMode.ANSWER and not answer:
            mode = AgentDecisionMode.UNKNOWN

        return AgentDecision(
            mode=mode,
            answer=answer,
            skill_name=skill_name,
            tool_name=tool_name,
            intent_name=intent_name,
            params=params,
            confidence=parsed_confidence,
            source="openclaw_runtime",
            raw_message=raw_message,
            rationale=rationale,
        )

    def _apply_request_guardrails(self, decision: AgentDecision, message: Message) -> AgentDecision:
        """Override obviously wrong model decisions for deterministic request types."""

        request = str(message.content or "").strip()
        basic_intent = self._build_basic_intent_guardrail(request)
        if basic_intent and decision.mode not in {AgentDecisionMode.SKILL, AgentDecisionMode.TOOL}:
            return AgentDecision(
                mode=AgentDecisionMode.INTENT,
                intent_name=basic_intent,
                params={},
                confidence=max(decision.confidence, 0.98),
                source="openclaw_runtime_guardrail",
                raw_message=message.content,
                rationale="basic_intent_guardrail",
            )

        file_read_params = self._build_file_read_intent_params(request)
        if file_read_params:
            current_mode = decision.mode
            current_params = dict(decision.params or {})
            if current_mode == AgentDecisionMode.INTENT:
                if str(decision.intent_name or "").strip().lower() == "read_file" and str(current_params.get("path") or "").strip():
                    return decision
            if current_mode == AgentDecisionMode.SKILL:
                resolved_skill = self._resolve_skill_name(decision.skill_name or "")
                resolved_repo_skill = self._resolve_skill_name("repo.fs")
                if resolved_skill and resolved_repo_skill and resolved_skill == resolved_repo_skill:
                    return decision

            # Route summarize requests to summarize-file skill instead of read_file
            _summarize_tokens = ("总结", "摘要", "概括", "summarize", "summarise", "summary")
            normalized_req = self._normalize_request_text(request)
            is_summarize_request = any(t in normalized_req for t in _summarize_tokens)
            summarize_file_skill = self._select_available_skill("summarize-file")
            if is_summarize_request and summarize_file_skill:
                # Use the deterministic path extraction (more reliable than LLM)
                # file_read_params already has normalized 'path' key
                path_value = file_read_params.get("path", "")
                return AgentDecision(
                    mode=AgentDecisionMode.SKILL,
                    skill_name=summarize_file_skill,
                    params={"path": path_value},
                    confidence=max(decision.confidence, 0.97),
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="summarize_file_skill_guardrail",
                )

            repo_fs_skill = self._select_available_skill("repo.fs", "workspace-files", "fs-workspace")
            if repo_fs_skill:
                return AgentDecision(
                    mode=AgentDecisionMode.SKILL,
                    skill_name=repo_fs_skill,
                    params={"action": "read", **file_read_params},
                    confidence=max(decision.confidence, 0.97),
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="filesystem_read_skill_guardrail",
                )
            return AgentDecision(
                mode=AgentDecisionMode.INTENT,
                intent_name="read_file",
                params=file_read_params,
                confidence=max(decision.confidence, 0.97),
                source="openclaw_runtime_guardrail",
                raw_message=message.content,
                rationale="filesystem_read_guardrail",
            )

        if (
            decision.mode == AgentDecisionMode.INTENT
            and str(decision.intent_name or "").strip().lower() in {"date_query", "time_now", "date_today"}
        ):
            # Guard against LLM overfitting on words like "tomorrow" in non-clock questions.
            # If the raw request is not actually asking for date/time, treat it as a general
            # factual query and route via normal web fallback instead of forcing clock intent.
            if not self._looks_like_clock_query(request):
                web_fallback = self._build_unknown_web_fallback_decision(request)
                if web_fallback is not None:
                    return web_fallback
                return decision

            params: Dict[str, Any] = {
                "request": request,
                "query": request,
                "q": request,
            }
            preferred_skill = self._select_available_skill(
                "web-access",
                "tavily-web-search",
                "agent-browser",
                "web.fetch",
            )
            if preferred_skill:
                return AgentDecision(
                    mode=AgentDecisionMode.SKILL,
                    skill_name=preferred_skill,
                    params=params,
                    confidence=max(decision.confidence, 0.95),
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="clock_query_web_guardrail",
                )

            browser_tool = self._lookup_tool("browser_cdp")
            if browser_tool is not None:
                return AgentDecision(
                    mode=AgentDecisionMode.TOOL,
                    tool_name="browser_cdp",
                    params=params,
                    confidence=max(decision.confidence, 0.9),
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="clock_query_web_guardrail",
                )

        desktop_listing_params = self._build_desktop_listing_intent_params(request)
        if desktop_listing_params:
            if desktop_listing_params.get("error"):
                return AgentDecision(
                    mode=AgentDecisionMode.ANSWER,
                    answer=desktop_listing_params["error"],
                    confidence=0.99,
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="missing_drive_letter",
                )
            if decision.mode not in {AgentDecisionMode.SKILL, AgentDecisionMode.TOOL}:
                return AgentDecision(
                    mode=AgentDecisionMode.INTENT,
                    intent_name="list_folders" if desktop_listing_params.get("folders_only") else "file_list",
                    params=desktop_listing_params,
                    confidence=max(decision.confidence, 0.97),
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="filesystem_listing_guardrail",
                )

        disk_space_params = self._build_disk_space_intent_params(request)
        if disk_space_params:
            if disk_space_params.get("error"):
                return AgentDecision(
                    mode=AgentDecisionMode.ANSWER,
                    answer=disk_space_params["error"],
                    confidence=0.99,
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="missing_drive_letter",
                )
            if decision.mode not in {AgentDecisionMode.SKILL, AgentDecisionMode.TOOL}:
                return AgentDecision(
                    mode=AgentDecisionMode.INTENT,
                    intent_name="check_disk_space",
                    params=disk_space_params,
                    confidence=max(decision.confidence, 0.97),
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="disk_usage_guardrail",
                )

        weather_params = self._build_weather_intent_params(request)
        if weather_params:
            if decision.mode in {AgentDecisionMode.SKILL, AgentDecisionMode.TOOL}:
                return decision
            preferred_weather_skill = self._select_available_skill(
                "weather.forecast",
                "weather",
                "forecast",
            )
            if preferred_weather_skill:
                skill_params: Dict[str, Any] = {
                    "request": request,
                    "query": request,
                }
                skill_params.update(weather_params)
                return AgentDecision(
                    mode=AgentDecisionMode.SKILL,
                    skill_name=preferred_weather_skill,
                    params=skill_params,
                    confidence=max(decision.confidence, 0.97),
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="weather_skill_guardrail",
                )
            if self._lookup_tool("http_get") is not None:
                return AgentDecision(
                    mode=AgentDecisionMode.INTENT,
                    intent_name="check_weather",
                    params=weather_params,
                    confidence=max(decision.confidence, 0.97),
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="weather_guardrail",
                )
            return AgentDecision(
                mode=AgentDecisionMode.ANSWER,
                answer="我现在不能直接感知外面的实时天气，而且当前没有可用的天气查询动作。",
                confidence=max(decision.confidence, 0.8),
                source="openclaw_runtime_guardrail",
                raw_message=message.content,
                rationale="weather_guardrail_no_tool",
            )

        if self._looks_like_clock_query(request):
            if decision.mode in {AgentDecisionMode.SKILL, AgentDecisionMode.TOOL}:
                return decision

            params: Dict[str, Any] = {
                "request": request,
                "query": request,
                "q": request,
            }
            extracted_url = self._extract_first_url(request)
            if extracted_url:
                params["url"] = extracted_url

            preferred_skill = self._select_available_skill(
                "web-access",
                "tavily-web-search",
                "agent-browser",
                "web.fetch",
            )
            if preferred_skill:
                return AgentDecision(
                    mode=AgentDecisionMode.SKILL,
                    skill_name=preferred_skill,
                    params=params,
                    confidence=max(decision.confidence, 0.95),
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="clock_query_web_guardrail",
                )

            browser_tool = self._lookup_tool("browser_cdp")
            if browser_tool is not None:
                return AgentDecision(
                    mode=AgentDecisionMode.TOOL,
                    tool_name="browser_cdp",
                    params=params,
                    confidence=max(decision.confidence, 0.9),
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="clock_query_web_guardrail",
                )

        if not self._looks_like_news_request(request):
            return decision

        rss_params = self._build_news_feed_tool_params(request)
        if rss_params:
            return AgentDecision(
                mode=AgentDecisionMode.TOOL,
                tool_name="http_get",
                params=rss_params,
                confidence=max(decision.confidence, 0.96),
                source="openclaw_runtime_guardrail",
                raw_message=message.content,
                rationale="live_news_rss_guardrail",
            )

        if decision.mode in {AgentDecisionMode.SKILL, AgentDecisionMode.TOOL}:
            return decision

        preferred_skill = self._select_available_skill(
            "web-access",
            "tavily-web-search",
            "agent-browser",
            "web.fetch",
        )
        params: Dict[str, Any] = {"request": request}
        extracted_url = self._extract_first_url(request)
        if extracted_url:
            params["url"] = extracted_url

        if preferred_skill:
            return AgentDecision(
                mode=AgentDecisionMode.SKILL,
                skill_name=preferred_skill,
                params=params,
                confidence=max(decision.confidence, 0.95),
                source="openclaw_runtime_guardrail",
                raw_message=message.content,
                rationale="live_news_guardrail",
            )

        browser_tool = self._lookup_tool("browser_cdp")
        if browser_tool is not None:
            return AgentDecision(
                mode=AgentDecisionMode.TOOL,
                tool_name="browser_cdp",
                params=params,
                confidence=max(decision.confidence, 0.9),
                source="openclaw_runtime_guardrail",
                raw_message=message.content,
                rationale="live_news_guardrail",
            )

        return decision

    def _build_fast_skill_guardrail(self, request: str, message: Message) -> Optional[AgentDecision]:
        """Fast-path pre-LLM guardrail for deterministic skill routing.

        Handles patterns that would normally require an LLM call to route, but
        can be resolved deterministically — primarily file-read and summarize-file requests.
        Returns None to fall through to the LLM when no fast path applies.
        """
        # Summarize-file: user wants to summarize a local file
        _summarize_tokens = ("总结", "摘要", "概括", "summarize", "summarise", "summary")
        normalized_req = self._normalize_request_text(request)
        is_summarize_request = any(t in normalized_req for t in _summarize_tokens)
        if is_summarize_request:
            file_read_params = self._build_file_read_intent_params(request)
            if file_read_params:
                summarize_file_skill = self._select_available_skill("summarize-file")
                if summarize_file_skill:
                    return AgentDecision(
                        mode=AgentDecisionMode.SKILL,
                        skill_name=summarize_file_skill,
                        params={"path": file_read_params.get("path", "")},
                        confidence=0.99,
                        source="openclaw_runtime_guardrail",
                        raw_message=message.content,
                        rationale="fast_summarize_file_guardrail",
                    )

        # File-read: user wants to open/read a local file
        file_read_params = self._build_file_read_intent_params(request)
        if file_read_params and not is_summarize_request:
            repo_fs_skill = self._select_available_skill("repo.fs", "workspace-files", "fs-workspace")
            if repo_fs_skill:
                return AgentDecision(
                    mode=AgentDecisionMode.SKILL,
                    skill_name=repo_fs_skill,
                    params={"action": "read", **file_read_params},
                    confidence=0.97,
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="fast_file_read_guardrail",
                )
            return AgentDecision(
                mode=AgentDecisionMode.INTENT,
                intent_name="read_file",
                params=file_read_params,
                confidence=0.97,
                source="openclaw_runtime_guardrail",
                raw_message=message.content,
                rationale="fast_file_read_guardrail",
            )

        # Desktop/drive listing
        desktop_listing_params = self._build_desktop_listing_intent_params(request)
        if desktop_listing_params:
            if desktop_listing_params.get("error"):
                return AgentDecision(
                    mode=AgentDecisionMode.ANSWER,
                    answer=desktop_listing_params["error"],
                    confidence=0.99,
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="fast_listing_guardrail",
                )
            return AgentDecision(
                mode=AgentDecisionMode.INTENT,
                intent_name="list_folders" if desktop_listing_params.get("folders_only") else "file_list",
                params=desktop_listing_params,
                confidence=0.97,
                source="openclaw_runtime_guardrail",
                raw_message=message.content,
                rationale="fast_listing_guardrail",
            )

        # Disk space
        disk_space_params = self._build_disk_space_intent_params(request)
        if disk_space_params:
            if disk_space_params.get("error"):
                return AgentDecision(
                    mode=AgentDecisionMode.ANSWER,
                    answer=disk_space_params["error"],
                    confidence=0.99,
                    source="openclaw_runtime_guardrail",
                    raw_message=message.content,
                    rationale="fast_disk_space_guardrail",
                )
            return AgentDecision(
                mode=AgentDecisionMode.INTENT,
                intent_name="check_disk_space",
                params=disk_space_params,
                confidence=0.97,
                source="openclaw_runtime_guardrail",
                raw_message=message.content,
                rationale="fast_disk_space_guardrail",
            )

        return None

    def _build_basic_intent_guardrail(self, request: str) -> Optional[str]:
        """Detect obvious greeting/help requests without invoking the model."""

        normalized = self._normalize_request_text(request)
        if not normalized or normalized.startswith("/"):
            return None

        if self._looks_like_clock_query(request):
            return None

        if re.fullmatch(r"(?:hi+|hello+|hey+|你好+|您好+|哈喽+|嗨+|在吗|在么|在嗎)[!,.?\s]*", normalized):
            return "greeting"

        help_markers = (
            "help",
            "你会什么",
            "你会做什么",
            "你能做什么",
            "你可以做什么",
            "有什么功能",
            "功能介绍",
            "怎么用",
        )
        if len(normalized) <= 32 and any(marker in normalized for marker in help_markers):
            return "help"

        time_markers = (
            "现在几点",
            "当前时间",
            "现在时间",
            "几点了",
            "time now",
            "what time is it",
            "current time",
        )
        if any(marker in normalized for marker in time_markers):
            return "time_now"

        date_markers = (
            "今天周几",
            "今天星期几",
            "今天几号",
            "今天日期",
            "几月几日",
            "today date",
            "what day is today",
            "weekday today",
        )
        if any(marker in normalized for marker in date_markers):
            return "date_query"

        if re.search(r"(今天|今日|today).*(周几|星期几|几号|日期|几月几日|weekday|date|day)", normalized):
            return "date_query"

        return None

    def _looks_like_clock_query(self, request: str) -> bool:
        """Detect date/time questions that should prefer live browser-based lookup."""

        normalized = self._normalize_request_text(request)
        if not normalized or normalized.startswith("/"):
            return False

        if self._build_file_read_intent_params(request):
            return False

        clock_markers = (
            "\u4eca\u5929\u5468\u51e0",
            "\u4eca\u5929\u661f\u671f\u51e0",
            "\u4eca\u5929\u51e0\u53f7",
            "\u4eca\u5929\u65e5\u671f",
            "\u660e\u5929\u5468\u51e0",
            "\u660e\u5929\u661f\u671f\u51e0",
            "\u540e\u5929\u5468\u51e0",
            "\u540e\u5929\u661f\u671f\u51e0",
            "\u6628\u5929\u5468\u51e0",
            "\u6628\u5929\u661f\u671f\u51e0",
            "\u4e0b\u5468\u51e0",
            "\u4e0b\u661f\u671f\u51e0",
            "\u4eca\u5929\u51e0\u70b9",
            "\u660e\u5929\u51e0\u70b9",
            "\u73b0\u5728\u51e0\u70b9",
            "\u73b0\u5728\u51e0\u70b9\u4e86",
            "\u5f53\u524d\u65f6\u95f4",
            "\u73b0\u5728\u65f6\u95f4",
            "\u4eca\u5929\u65e5\u671f\u662f\u4ec0\u4e48",
            "\u51e0\u6708\u51e0\u65e5",
            "time now",
            "what time is it",
            "what day is today",
            "what day is tomorrow",
            "what day is it tomorrow",
            "what date is tomorrow",
            "today date",
            "weekday today",
            "date today",
            "current time",
            "current date",
        )
        if any(marker in normalized for marker in clock_markers):
            return True

        return bool(
            re.search(
                r"(?:\u4eca\u5929|\u4eca\u65e5|\u660e\u5929|\u540e\u5929|\u6628\u5929|\u4e0b\u5468|\u4e0b\u661f\u671f|today|tomorrow|yesterday|now).*(?:\u5468\u51e0|\u661f\u671f|\u51e0\u53f7|\u65e5\u671f|\u51e0\u6708\u51e0\u65e5|\u51e0\u70b9|weekday|date|day|time)",
                normalized,
            )
        )

    def _select_available_skill(self, *identifiers: str) -> Optional[str]:
        """Return the first model-invocable available skill from the preference list."""

        registry = self._get_skill_registry()
        if registry is None:
            return None

        try:
            allowed = {
                str(item.get("name") or "").strip()
                for item in registry.get_model_invocable_info()
            }
        except Exception:
            allowed = set()

        for identifier in identifiers:
            resolved = self._resolve_skill_name(identifier)
            if not resolved:
                continue
            try:
                skill = registry.get(resolved)
            except Exception:
                skill = None
            if skill is None:
                continue

            if allowed and resolved not in allowed:
                definition = getattr(skill, "get_definition", lambda: None)()
                canonical_name = str(getattr(definition, "name", "") or "").strip()
                metadata = getattr(definition, "metadata", {}) if definition is not None else {}
                skill_key = str((metadata or {}).get("skill_key", "")).strip()
                aliases = {str(alias).strip() for alias in (metadata or {}).get("aliases", [])}
                if (
                    canonical_name not in allowed
                    and skill_key not in allowed
                    and not aliases.intersection(allowed)
                ):
                    continue

            state = getattr(skill, "state", None)
            if state is not None:
                state_value = str(getattr(state, "value", state)).strip().lower()
                if state_value not in {"enabled", "running"}:
                    continue
            return resolved
        return None

    def _looks_like_news_request(self, text: str) -> bool:
        """Detect user requests that clearly require current news or headlines."""

        normalized = self._normalize_request_text(text)
        if not normalized:
            return False

        news_keywords = (
            "新闻",
            "头条",
            "资讯",
            "快讯",
            "热点",
            "热搜",
            "news",
            "headline",
            "headlines",
            "breaking",
        )
        current_keywords = (
            "今天",
            "今日",
            "最新",
            "刚刚",
            "最近",
            "当前",
            "实时",
            "today",
            "latest",
            "recent",
            "current",
            "now",
        )

        has_news = any(keyword in normalized for keyword in news_keywords)
        has_current = any(keyword in normalized for keyword in current_keywords)
        return has_news and (has_current or "个" in normalized or "条" in normalized or "10" in normalized)

    def _build_unknown_web_fallback_decision(self, request: str) -> Optional[AgentDecision]:
        """Route unknown factual questions to an installed web-search skill when available."""

        normalized = self._normalize_request_text(request)
        if not self._should_try_unknown_web_fallback(request, normalized):
            return None

        params: Dict[str, Any] = {
            "request": request,
            "query": request,
            "q": request,
        }
        extracted_url = self._extract_first_url(request)
        if extracted_url:
            params["url"] = extracted_url

        preferred_skill = self._select_available_skill(
            "web-access",
            "tavily-web-search",
            "agent-browser",
            "web.fetch",
        )
        if preferred_skill:
            return AgentDecision(
                mode=AgentDecisionMode.SKILL,
                skill_name=preferred_skill,
                params=params,
                confidence=0.78,
                source="openclaw_runtime_web_fallback",
                raw_message=request,
                rationale="unknown_question_web_fallback",
            )

        browser_tool = self._lookup_tool("browser_cdp")
        if browser_tool is not None:
            return AgentDecision(
                mode=AgentDecisionMode.TOOL,
                tool_name="browser_cdp",
                params=params,
                confidence=0.7,
                source="openclaw_runtime_web_fallback",
                raw_message=request,
                rationale="unknown_question_web_fallback",
            )

        return None

    def _should_try_unknown_web_fallback(self, request: str, normalized: str) -> bool:
        """Decide whether an unknown request should attempt a web-search skill fallback."""

        if not normalized or normalized.startswith("/"):
            return False

        if self._build_basic_intent_guardrail(request):
            return False

        if self._looks_like_news_request(request):
            return False
        if self._build_weather_intent_params(request):
            return False
        if self._build_desktop_listing_intent_params(request):
            return False
        if self._build_file_read_intent_params(request):
            return False
        if self._build_disk_space_intent_params(request):
            return False

        if self._looks_like_clock_query(request):
            return True

        local_clock_markers = (
            "周几",
            "星期几",
            "几号",
            "几月几日",
            "现在几点",
            "当前时间",
            "time now",
            "today date",
            "date today",
        )
        if any(marker in normalized for marker in local_clock_markers):
            return True

        casual_markers = (
            "随便聊",
            "聊聊",
            "你怎么看",
            "你觉得",
            "谈谈",
            "写一段",
            "润色",
            "改写",
            "翻译",
            "总结",
            "解释一下代码",
            "讲个笑话",
            "闲聊",
        )
        if any(marker in normalized for marker in casual_markers):
            return False

        has_question_mark = ("?" in request) or ("？" in request)
        question_terms = (
            "是什么",
            "什么意思",
            "为什么",
            "怎么",
            "如何",
            "谁是",
            "哪里",
            "哪儿",
            "哪国",
            "哪位",
            "what",
            "why",
            "how",
            "who",
            "where",
            "when",
            "which",
        )
        has_question_terms = any(term in normalized for term in question_terms)
        return has_question_mark or has_question_terms

    def _build_desktop_listing_intent_params(self, request: str) -> Optional[Dict[str, Any]]:
        """Detect simple desktop or drive listing requests that can be routed deterministically."""

        normalized = self._normalize_request_text(request)
        if not normalized or normalized.startswith("/"):
            return None

        if self._extract_first_url(request):
            return None

        wants_desktop = any(token in normalized for token in ("桌面", "desktop"))
        wants_listing = any(
            token in normalized
            for token in ("看看", "查看", "列出", "显示", "有哪些", "有那些", "有什么", "都有什么", "内容")
        )
        wants_folders_only = any(
            re.search(pattern, normalized, re.IGNORECASE)
            for pattern in (r"文件夹", r"目录", r"\bfolders?\b", r"\bdirector(?:y|ies)\b")
        )
        wants_files = any(
            re.search(pattern, normalized, re.IGNORECASE)
            for pattern in (r"文件(?!夹)", r"文档", r"\bfiles?\b")
        )

        if wants_desktop:
            if not wants_listing and not wants_folders_only and not wants_files:
                return None
            return {
                "path": "~/Desktop",
                "folders_only": wants_folders_only and not wants_files,
            }

        drive_match = re.search(r"([a-z])\s*盘", normalized)
        if drive_match:
            drive_letter = drive_match.group(1).upper()
            if not wants_listing and not wants_folders_only and not wants_files:
                return None
            return {
                "path": f"{drive_letter}:/",
                "folders_only": wants_folders_only and not wants_files,
            }

        if "盘" in normalized and wants_folders_only:
            return {
                "path": "ask_drive",
                "folders_only": True,
                "error": "请指定盘符，例如：D盘有哪些文件夹？",
            }

        return None

    def _build_file_read_intent_params(self, request: str) -> Optional[Dict[str, Any]]:
        """Detect direct file-opening requests that should read local text files."""

        normalized = self._normalize_request_text(request)
        if not normalized or normalized.startswith("/"):
            return None

        if self._extract_first_url(request):
            return None

        if not any(token in normalized for token in ("打开", "查看", "读取", "读一下", "看看", "总结", "翻译", "分析", "解释", "摘要")):
            return None

        # Strip trailing context suffixes (loop to handle stacked suffixes like "这个文件的内容")
        _suffix_pattern = re.compile(
            r"(?:这个文件|那个文件|这份文件|那份文件|这个|那个|这份|那份|的内容|的信息|内容|信息|文件)\s*$"
        )
        stripped_request = request
        for _ in range(4):
            new_stripped = _suffix_pattern.sub("", stripped_request).strip()
            if new_stripped == stripped_request:
                break
            stripped_request = new_stripped

        _desktop_re = re.compile(
            r"(?:桌面|desktop)(?:上|里的|中的|的|上的)?\s*(?:的\s*)?(?P<filename>[^\\/:*?\"<>|\r\n\s]+?\.[A-Za-z0-9]+)\s*$",
            re.IGNORECASE,
        )
        file_match = _desktop_re.search(stripped_request)
        if file_match:
            filename = str(file_match.group("filename") or "").strip()
            if filename:
                return {"path": f"~/Desktop/{filename}"}

        # For generic (non-desktop, non-absolute) filenames, find all tokens with an extension
        # and take the rightmost one to avoid greedily capturing leading verbs.
        abs_path_match = re.search(
            r"(?P<path>[A-Za-z]:[/\\].+?|~?/[^/:*?\"<>|\r\n]+)\s*$",
            stripped_request,
            re.IGNORECASE,
        )
        if abs_path_match:
            path = str(abs_path_match.group("path") or "").strip()
            if path:
                return {"path": path}

        # Collect all tokens that look like filenames (contain a dot with extension).
        # First strip known action verb prefixes so they don't get included in the filename token.
        _verb_prefix_pattern = re.compile(
            r"^(?:帮我|给我|请|总结一下|翻译一下|分析一下|解释一下|摘要一下|总结|翻译|分析|解释|摘要|打开|查看|读取|读一下|看看)\s*"
        )
        stripped_for_tokens = _verb_prefix_pattern.sub("", stripped_request)
        filename_tokens = re.findall(
            r"[^\s\\/:*?\"<>|\r\n]+\.[A-Za-z0-9]+",
            stripped_for_tokens,
        )
        if filename_tokens:
            path = str(filename_tokens[-1]).strip()
            if path:
                return {"path": path}

        return None

    def _build_disk_space_intent_params(self, request: str) -> Optional[Dict[str, Any]]:
        """Detect drive free-space requests that can be routed deterministically."""

        normalized = self._normalize_request_text(request)
        if not normalized or normalized.startswith("/"):
            return None

        if self._extract_first_url(request):
            return None

        space_keywords = (
            "空间",
            "容量",
            "磁盘空间",
            "磁盘容量",
            "剩余",
            "还剩",
            "剩多少",
            "可用",
            "空闲",
            "剩下",
            "free space",
            "available space",
            "disk space",
        )
        if not any(keyword in normalized for keyword in space_keywords):
            return None

        drive_match = re.search(r"([a-z])\s*盘", normalized)
        if drive_match:
            drive_letter = drive_match.group(1).upper()
            return {
                "path": f"{drive_letter}:/",
                "drive": drive_letter,
            }

        if "盘" in normalized or "磁盘" in normalized:
            return {
                "path": "ask_drive",
                "error": "请指定盘符，例如：C盘空间还剩多少？",
            }

        return None

    def _build_weather_intent_params(self, request: str) -> Optional[Dict[str, Any]]:
        """Detect weather-like requests that should route to the live weather tool path."""

        normalized = self._normalize_request_text(request)
        if not normalized or normalized.startswith("/"):
            return None

        if self._extract_first_url(request):
            return None

        weather_keywords = (
            "天气",
            "气温",
            "温度",
            "下雨",
            "下雪",
            "晴天",
            "阴天",
            "多云",
            "冷不",
            "热不",
            "冷不冷",
            "热不热",
            "冷吗",
            "热吗",
            "风大",
            "风力",
            "风小",
            "雾霾",
        )
        weather_patterns = (
            r"(?:外面|窗外).*(?:天|天气|云|雨|雪|晴|阴|蓝)",
            r"(?:天|天气).*(?:蓝|晴|阴|云|雨|雪)",
            r"(?:今天|明天|后天).*(?:热不|冷不|热吗|冷吗|热不热|冷不冷|下雨|下雪|天气|温度|气温)",
        )
        if not any(keyword in normalized for keyword in weather_keywords) and not any(
            re.search(pattern, normalized, re.IGNORECASE) for pattern in weather_patterns
        ):
            return None

        day_offset = 0
        day_label = "今天"
        if "大后天" in normalized:
            day_offset = 3
            day_label = "大后天"
        elif "后天" in normalized:
            day_offset = 2
            day_label = "后天"
        elif "明天" in normalized:
            day_offset = 1
            day_label = "明天"

        return {
            "location": self._extract_weather_location(request),
            "day_offset": day_offset,
            "day_label": day_label,
        }

    def _extract_weather_location(self, request: str) -> str:
        """Best-effort extraction of a location label from a weather question."""

        normalized = unicodedata.normalize("NFKC", str(request or ""))
        normalized = re.sub(r"https?://[^\s<>()\"']+", " ", normalized, flags=re.IGNORECASE)
        filler_patterns = (
            r"帮我",
            r"给我",
            r"请",
            r"查下",
            r"查一下",
            r"看下",
            r"看看",
            r"查看",
            r"告诉我",
            r"想知道",
            r"问下",
            r"搜下",
            r"搜一下",
        )
        for pattern in filler_patterns:
            normalized = re.sub(pattern, " ", normalized, flags=re.IGNORECASE)

        compact = re.sub(r"\s+", "", normalized)
        compact = re.sub(r"^(?:今天|明天|后天|现在|此刻)+", "", compact)
        match = re.search(
            r"(?P<location>[\u4e00-\u9fffA-Za-z]{1,20}?)(?:今天|明天|后天|现在|此刻)?(?:的)?"
            r"(?:天气|气温|温度|会不会下雨|会下雨|下不下雨|下雨|下雪|冷不|热不|冷不冷|热不热|冷吗|热吗|风大不大|风大|风力)",
            compact,
        )
        if not match:
            return ""

        location = re.sub(r"^(?:大后天|今天|明天|后天|现在|此刻)+", "", match.group("location"))
        if len(location.strip()) <= 1:
            return ""
        if location in {
            "大后天",
            "今天",
            "明天",
            "后天",
            "现在",
            "此刻",
            "外面",
            "窗外",
            "这里",
            "那边",
            "当地",
            "本地",
            "这边",
            "天气",
            "天",
        }:
            return ""
        return location

    def _build_news_feed_tool_params(self, request: str) -> Optional[Dict[str, Any]]:
        """Return an http_get request for generic headline queries when possible."""

        if not request or request.lstrip().startswith("/"):
            return None

        if self._lookup_tool("http_get") is None:
            return None

        if self._extract_first_url(request):
            return None

        if re.search(r"\b[a-z0-9-]+\.(?:com|cn|net|org|io|gov|edu)(?:[/?#:]|\b)", request, re.IGNORECASE):
            return None

        limit = self._extract_news_item_limit(request)
        topic = self._extract_news_topic(request)
        if topic:
            query = f"{topic} when:1d"
            url = f"https://news.google.com/rss/search?q={quote_plus(query)}&{NEWS_FEED_LOCALE}"
        else:
            url = f"https://news.google.com/rss?{NEWS_FEED_LOCALE}"

        return {
            "url": url,
            "headers": {
                "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8",
            },
            "limit": limit,
            "request": request,
            "format_hint": "rss_news",
            **({"topic": topic} if topic else {}),
        }

    def _extract_news_item_limit(self, text: str) -> int:
        """Extract the requested number of headlines, defaulting to ten."""

        match = re.search(r"(\d{1,2})\s*(?:个|条)?", text)
        if match:
            try:
                return max(1, min(int(match.group(1)), 20))
            except ValueError:
                pass

        chinese_counts = {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        for token, value in chinese_counts.items():
            if token in text:
                return value
        return 10

    def _extract_news_topic(self, text: str) -> str:
        """Best-effort extraction of a topical news query from a free-form request."""

        normalized = re.sub(r"https?://[^\s<>()\"']+", " ", text, flags=re.IGNORECASE)
        stop_patterns = (
            r"从网上",
            r"联网",
            r"上网",
            r"给我",
            r"帮我",
            r"请",
            r"把",
            r"拿来",
            r"来点",
            r"看下",
            r"看看",
            r"搜一下",
            r"搜索",
            r"查一下",
            r"查查",
            r"告诉我",
            r"列出",
            r"整理",
            r"最新的?",
            r"今天的?",
            r"今日的?",
            r"最近的?",
            r"当前的?",
            r"实时的?",
            r"新闻",
            r"头条",
            r"资讯",
            r"快讯",
            r"热点",
            r"热搜",
            r"latest",
            r"recent",
            r"current",
            r"today(?:'s)?",
            r"news",
            r"headline(?:s)?",
            r"breaking",
            r"top",
        )
        for pattern in stop_patterns:
            normalized = re.sub(pattern, " ", normalized, flags=re.IGNORECASE)

        normalized = re.sub(r"\d{1,2}\s*(?:个|条)?", " ", normalized)
        normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", normalized)
        normalized = re.sub(r"\b(?:the|me|please|show|find|get|some|for|about)\b", " ", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+", " ", normalized).strip(" _")
        if not normalized:
            return ""
        if len(normalized) <= 1:
            return ""
        return normalized[:80]

    def _extract_first_url(self, text: str) -> Optional[str]:
        """Extract the first absolute URL from user text."""

        match = re.search(r"https?://[^\s<>()\"']+", text, re.IGNORECASE)
        if not match:
            return None
        return match.group(0).rstrip(".,)")

    def _normalize_request_text(self, text: str) -> str:
        """Normalize user text for robust guardrail matching."""

        normalized = unicodedata.normalize("NFKC", str(text or ""))
        normalized = normalized.replace("\\", "/")
        normalized = re.sub(r"\s+", " ", normalized).strip().lower()
        return normalized

    def _resolve_skill_name(self, identifier: Any) -> Optional[str]:
        """Resolve a skill by canonical name, skill key, or alias."""

        normalized = str(identifier or "").strip()
        if not normalized:
            return None
        registry = self._get_skill_registry()
        if registry is not None and hasattr(registry, "resolve_name"):
            resolved = registry.resolve_name(normalized)
            return resolved or normalized
        return normalized

    def _extract_json_block(self, content: str) -> Optional[str]:
        """Extract a JSON object from model output."""
        return extract_last_json_object(content)

    def _xml_escape(self, value: str) -> str:
        """Escape XML-sensitive characters for prompt sections."""

        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    def _json_escape(self, value: str) -> str:
        """Escape a string for safe inline JSON examples."""

        return value.replace("\\", "\\\\").replace('"', '\\"')
