"""Security review helpers for third-party skill installation."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional


_SENSITIVE_INPUT_PATTERN = re.compile(
    r"(api[_-]?key|access[_-]?token|client[_-]?secret|secret|password|credential|ssh[_-]?key|private[_-]?key|密钥|秘钥|口令|凭证|私钥)",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
_SECRET_REQUEST_KEYWORDS = [
    "api key",
    "access token",
    "client secret",
    "private key",
    "ssh key",
    "ssh private key",
    "密钥",
    "秘钥",
    "secret",
    "token",
    "凭证",
    "私钥",
]
_COMMAND_EXEC_KEYWORDS = [
    "执行命令",
    "运行命令",
    "系统管理",
    "自动化运维",
    "terminal",
    "shell",
    "bash",
    "powershell",
    "run command",
    "command execution",
    "subprocess",
]
_BROWSER_CONTROL_KEYWORDS = [
    "浏览器控制",
    "网页自动化",
    "自动点击",
    "browser automation",
    "browser control",
    "playwright",
    "selenium",
    "webdriver",
    "puppeteer",
    "headless",
    "cdp",
    "chrome devtools",
]
_FILE_ACCESS_KEYWORDS = [
    "读取文件",
    "访问文件系统",
    "file system",
    "filesystem",
    "read file",
    "file access",
    "目录遍历",
]
_NETWORK_REQUEST_KEYWORDS = [
    "访问网络",
    "发送请求",
    "network request",
    "http request",
    "api 调用",
    "api调用",
    "访问接口",
    "联网",
    "webhook",
]
_SCHEDULER_KEYWORDS = [
    "定时任务",
    "定时执行",
    "后台运行",
    "后台执行",
    "cron",
    "schedule",
    "scheduler",
    "interval",
    "apscheduler",
    "task scheduler",
    "daemon",
    "守护进程",
]

_NETWORK_EGRESS_KEYWORDS = [
    "http_post",
    "requests.post",
    "aiohttp",
    "webhook",
    "fetch(",
    "authorization",
    "bearer ",
    "upload",
    "send(",
]
_MINER_KEYWORDS = [
    "xmrig",
    "miner",
    "mining",
    "stratum",
    "coinhive",
    "cryptonight",
    "monero",
    "pool.minexmr",
]
_REMOTE_EXEC_KEYWORDS = [
    "curl ",
    "wget ",
    "requests.get",
    "urllib.request",
    "invoke-webrequest",
    "downloadstring",
    "powershell",
    "subprocess",
    "exec(",
    "eval(",
    "git clone",
    "pip install",
    "npm install",
    "raw.githubusercontent.com",
]
_SENSITIVE_PATH_KEYWORDS = [
    ".env",
    ".ssh",
    "id_rsa",
    "known_hosts",
    "credentials",
    "token",
    "appdata",
    "keychain",
]
_REMOTE_CONTENT_KEYWORDS = [
    "http_get",
    "browser",
    "browser_cdp",
    "scrape",
    "crawler",
    "crawl",
    "beautifulsoup",
    "selenium",
    "playwright",
    "urlopen",
]
_DANGEROUS_TOOLS = {
    "shell",
    "safe_shell",
    "browser_cdp",
    "file_delete",
    "file_write",
    "http_post",
    "http_get",
}
_COMMAND_EXECUTION_TOOLS = {
    "shell",
    "safe_shell",
}
_FILE_ACCESS_TOOLS = {
    "file_read",
    "file_list",
    "file_write",
    "file_append",
    "file_delete",
    "file_mkdir",
}
_NETWORK_REQUEST_TOOLS = {
    "http",
    "http_get",
    "http_post",
    "browser_cdp",
}
_BENIGN_USE_CASE_KEYWORDS = [
    "weather",
    "date",
    "time",
    "translate",
    "summary",
    "report",
    "readme",
    "markdown",
    "todo",
    "note",
    "calculator",
]
_IMPERSONATION_KEYWORDS = [
    "official",
    "browser-pro",
    "memory-plus",
    "premium",
    "enterprise",
    "copilot",
    "openai",
    "anthropic",
    "wechat",
    "whatsapp",
    "github",
]

_RISK_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "极高",
}

_SEVERITY_WEIGHT = {
    "low": 2,
    "medium": 4,
    "high": 7,
    "critical": 10,
}

_POST_INSTALL_PROTECTED_TOOLS = {
    "shell",
    "safe_shell",
    "browser",
    "browser_cdp",
    "playwright",
    "selenium",
    "webdriver",
    "puppeteer",
    "file_read",
    "file_list",
    "file_write",
    "file_append",
    "file_delete",
    "file_mkdir",
    "http",
    "http_get",
    "http_post",
}
_POST_INSTALL_CRITICAL_TOOLS = {
    "shell",
}


def review_skill_installation(
    skill_id: str,
    detail: Optional[Dict[str, Any]] = None,
    bundle: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Review a remote skill bundle before installation."""

    detail = detail if isinstance(detail, dict) else {}
    bundle = bundle if isinstance(bundle, dict) else {}

    metadata = _extract_metadata(skill_id, detail, bundle)
    corpus = _build_corpus(detail, bundle)

    findings = [
        finding
        for finding in [
            _detect_sensitive_secret_input(metadata, corpus),
            _detect_command_execution_capability(metadata, corpus),
            _detect_browser_control_capability(metadata, corpus),
            _detect_file_access_capability(metadata, corpus),
            _detect_network_request_capability(metadata, corpus),
            _detect_scheduled_execution_capability(metadata, corpus),
            _detect_secret_harvest(metadata, corpus),
            _detect_miner(metadata, corpus),
            _detect_dynamic_pull(metadata, corpus),
            _detect_overprivilege(metadata, corpus),
            _detect_impersonation(metadata),
            _detect_missing_provenance(metadata),
            _detect_third_party_content(metadata, corpus),
            _detect_unmaintained(metadata),
        ]
        if finding is not None
    ]

    risk_score = sum(int(finding["score"]) for finding in findings)
    risk_level = _resolve_risk_level(findings, risk_score)
    recommended_action = _resolve_recommended_action(risk_level)
    status = "pass" if not findings else ("block" if recommended_action == "block" else "warn")

    recommendations = _build_recommendations(findings, risk_level)
    install_options = _build_install_options(risk_level)
    summary = _build_summary(metadata, findings, risk_level, recommended_action)

    checks = {
        "author_present": bool(metadata["author"]),
        "homepage_present": bool(metadata["homepage"]),
        "repository_present": bool(metadata["repository"]),
        "documentation_present": metadata["documentation_present"],
        "dangerous_tool_detected": any(tool in _DANGEROUS_TOOLS for tool in metadata["tools"]),
        "remote_fetch_detected": bool(_keyword_hits(corpus, _REMOTE_CONTENT_KEYWORDS)),
        "sensitive_input_detected": bool(metadata["sensitive_inputs"]),
        "command_execution_detected": bool(_matching_tools(metadata["tools"], _COMMAND_EXECUTION_TOOLS))
        or bool(_keyword_hits(corpus, _COMMAND_EXEC_KEYWORDS)),
        "browser_control_detected": bool(_matching_tools(metadata["tools"], {"browser", "browser_cdp", "playwright", "selenium", "webdriver", "puppeteer"}))
        or bool(_keyword_hits(corpus, _BROWSER_CONTROL_KEYWORDS)),
        "file_access_detected": bool(_matching_tools(metadata["tools"], _FILE_ACCESS_TOOLS))
        or bool(_keyword_hits(corpus, _FILE_ACCESS_KEYWORDS)),
        "network_request_detected": bool(_matching_tools(metadata["tools"], _NETWORK_REQUEST_TOOLS))
        or bool(_keyword_hits(corpus, _NETWORK_REQUEST_KEYWORDS)),
        "scheduler_detected": bool(metadata["trigger_signals"]) or bool(_keyword_hits(corpus, _SCHEDULER_KEYWORDS)),
    }

    return {
        "skill_id": skill_id,
        "skill_name": metadata["name"],
        "version": metadata["version"],
        "risk_level": risk_level,
        "risk_label": _RISK_LABELS[risk_level],
        "risk_score": risk_score,
        "status": status,
        "recommended_action": recommended_action,
        "summary": summary,
        "findings": findings,
        "recommendations": recommendations,
        "install_options": install_options,
        "metadata_snapshot": {
            "author": metadata["author"],
            "homepage": metadata["homepage"],
            "repository": metadata["repository"],
            "downloads": metadata["downloads"],
            "stars": metadata["stars"],
            "updated_at": metadata["updated_at"],
            "tools": metadata["tools"],
            "inputs": metadata["inputs"],
            "triggers": metadata["trigger_signals"],
        },
        "checks": checks,
        "scan_version": 1,
    }


def build_post_install_guard(
    bundle: Optional[Dict[str, Any]],
    scan: Dict[str, Any],
    protection_mode: str,
    isolation_require_approval: bool = True,
    isolation_block_critical: bool = True,
) -> Dict[str, Any]:
    """Build a persistent post-install guard policy for a skill bundle."""

    bundle = bundle if isinstance(bundle, dict) else {}
    metadata = _extract_metadata(scan.get("skill_id", "unknown"), {}, bundle)
    tool_names = metadata.get("tools", [])
    protected_tools = _matching_tools(tool_names, _POST_INSTALL_PROTECTED_TOOLS)
    critical_tools = _matching_tools(tool_names, _POST_INSTALL_CRITICAL_TOOLS)
    has_triggers = bool(metadata.get("trigger_signals"))

    guard: Dict[str, Any] = {
        "mode": protection_mode,
        "protected_tools": protected_tools,
        "blocked_tools": [],
        "approval_required_tools": [],
        "disable_triggers": False,
        "reason": "",
        "risk_level": scan.get("risk_level", "low"),
    }

    if protection_mode == "disable_high_risk":
        guard["blocked_tools"] = protected_tools
        guard["disable_triggers"] = has_triggers
        guard["reason"] = "Installed with post-install protection: high-risk capabilities are disabled by default."
    elif protection_mode == "isolate":
        guard["blocked_tools"] = critical_tools if isolation_block_critical else []
        guard["approval_required_tools"] = protected_tools if isolation_require_approval else []
        guard["disable_triggers"] = True
        guard["reason"] = "Installed in restricted isolation mode: protected tools require approval and automatic triggers are disabled."
    else:
        guard["reason"] = "No post-install protection policy applied."

    return guard


def apply_post_install_guard(bundle: Dict[str, Any], guard: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a post-install guard policy onto a skill bundle."""

    prepared = dict(bundle)
    metadata = dict(prepared.get("metadata", {})) if isinstance(prepared.get("metadata"), dict) else {}
    metadata["localclaw_guard"] = guard
    prepared["metadata"] = metadata

    if guard.get("disable_triggers") and isinstance(prepared.get("triggers"), list):
        metadata["disabled_triggers"] = list(prepared.get("triggers", []))
        prepared["triggers"] = []

    return prepared


def _build_summary(
    metadata: Dict[str, Any],
    findings: List[Dict[str, Any]],
    risk_level: str,
    recommended_action: str,
) -> str:
    """Build a user-facing summary."""

    if not findings:
        return (
            f"未发现 {metadata['name']} 存在已知恶意插件特征，"
            "也未检测到你重点关注的高危能力项，但仍建议在安装前确认作者、源码和权限范围。"
        )

    action_label = {
        "allow": "可以继续安装",
        "review": "建议先人工审查后再安装",
        "block": "建议取消安装",
    }[recommended_action]
    return (
        f"安全体检发现 {len(findings)} 类风险信号，整体风险等级为{_RISK_LABELS[risk_level]}。"
        f"{action_label}。"
    )


def _build_recommendations(findings: List[Dict[str, Any]], risk_level: str) -> List[str]:
    """Build recommendations from findings."""

    suggestions = [
        "安装前确认作者、主页、仓库和更新记录是否可信。",
        "优先选择权限最小、说明完整、可审计的 skill。",
    ]
    if risk_level in {"high", "critical"}:
        suggestions.insert(0, "当前风险偏高，建议先查看源码或直接取消安装。")
    elif risk_level == "medium":
        suggestions.insert(0, "建议先人工审查源码和权限，再决定是否继续安装。")

    for finding in findings:
        advice = finding.get("advice", "").strip()
        if advice and advice not in suggestions:
            suggestions.append(advice)
    return suggestions


def _build_install_options(risk_level: str) -> List[Dict[str, Any]]:
    """Build explicit install choices for the UI."""

    if risk_level in {"high", "critical"}:
        proceed_label = "我已知风险，仍然安装"
    elif risk_level == "medium":
        proceed_label = "我已审查，继续安装"
    else:
        proceed_label = "继续安装"

    return [
        {
            "id": "cancel",
            "label": "取消安装",
            "description": "放弃本次安装，保持当前环境不变。",
            "recommended": risk_level in {"high", "critical"},
        },
        {
            "id": "review_source",
            "label": "先看源码",
            "description": "先人工检查 skill 源码、作者和权限，再决定是否安装。",
            "recommended": risk_level == "medium",
        },
        {
            "id": "proceed",
            "label": proceed_label,
            "description": "接受当前体检结果，继续执行安装。",
            "recommended": risk_level == "low",
        },
    ]


def _resolve_risk_level(findings: List[Dict[str, Any]], risk_score: int) -> str:
    """Resolve the overall risk level."""

    severities = {finding["severity"] for finding in findings}
    if "critical" in severities or risk_score >= 10:
        return "critical"
    if "high" in severities or risk_score >= 7:
        return "high"
    if "medium" in severities or risk_score >= 4:
        return "medium"
    return "low"


def _resolve_recommended_action(risk_level: str) -> str:
    """Resolve the suggested action from risk level."""

    if risk_level == "critical":
        return "block"
    if risk_level in {"medium", "high"}:
        return "review"
    return "allow"


def _extract_metadata(skill_id: str, detail: Dict[str, Any], bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Extract normalized metadata from skill detail and bundle."""

    detail_meta = detail.get("metadata", {}) if isinstance(detail.get("metadata"), dict) else {}
    bundle_meta = bundle.get("metadata", {}) if isinstance(bundle.get("metadata"), dict) else {}
    detail_stats = detail.get("stats", {}) if isinstance(detail.get("stats"), dict) else {}
    files = bundle.get("files", {}) if isinstance(bundle.get("files"), dict) else {}

    name = _first_nonempty(
        bundle.get("name"),
        detail.get("name"),
        detail_meta.get("name"),
        skill_id,
    ) or skill_id
    version = _first_nonempty(bundle.get("version"), detail.get("version"), "1.0.0") or "1.0.0"
    description = _first_nonempty(
        bundle.get("description"),
        detail.get("description"),
        bundle_meta.get("description"),
        detail_meta.get("description"),
        "",
    ) or ""
    author = _first_nonempty(
        detail.get("author"),
        bundle.get("author"),
        detail_meta.get("author"),
        bundle_meta.get("author"),
        detail.get("owner"),
        "",
    ) or ""
    homepage = _first_nonempty(
        detail.get("homepage"),
        bundle.get("homepage"),
        detail.get("url"),
        detail_meta.get("homepage"),
        bundle_meta.get("homepage"),
        "",
    ) or ""
    repository = _first_nonempty(
        detail.get("repository"),
        detail.get("repo"),
        bundle.get("repository"),
        detail_meta.get("repository"),
        bundle_meta.get("repository"),
        "",
    ) or ""
    documentation_present = bool(
        _first_nonempty(
            detail.get("readme"),
            detail.get("documentation"),
            bundle_meta.get("documentation"),
            files.get("SKILL.md"),
            files.get("README.md"),
            description,
        )
    )
    tools = sorted(set(_collect_tool_names(detail) + _collect_tool_names(bundle)))
    inputs = sorted(set(_collect_input_names(detail) + _collect_input_names(bundle)))
    trigger_signals = sorted(set(_collect_trigger_signals(detail) + _collect_trigger_signals(bundle)))
    permissions = {
        **(detail.get("permissions", {}) if isinstance(detail.get("permissions"), dict) else {}),
        **(bundle.get("permissions", {}) if isinstance(bundle.get("permissions"), dict) else {}),
    }
    sensitive_inputs = [name for name in inputs if _SENSITIVE_INPUT_PATTERN.search(name)]

    downloads = _coerce_stat(
        _first_nonempty(
            detail.get("downloads"),
            detail_stats.get("downloads"),
            detail_meta.get("downloads"),
        )
    )
    stars = _coerce_stat(
        _first_nonempty(
            detail.get("stars"),
            detail_stats.get("stars"),
            detail_meta.get("stars"),
        )
    )
    reviews = _coerce_stat(
        _first_nonempty(
            detail.get("reviews"),
            detail.get("review_count"),
            detail_stats.get("reviews"),
            detail_stats.get("review_count"),
        )
    )
    updated_at = _first_nonempty(
        detail.get("updated_at"),
        detail.get("last_updated"),
        detail_meta.get("updated_at"),
        detail_meta.get("last_updated"),
        "",
    ) or ""

    return {
        "name": str(name),
        "version": str(version),
        "description": str(description),
        "author": str(author),
        "homepage": str(homepage),
        "repository": str(repository),
        "documentation_present": documentation_present,
        "tools": tools,
        "inputs": inputs,
        "trigger_signals": trigger_signals,
        "permissions": permissions,
        "sensitive_inputs": sensitive_inputs,
        "downloads": downloads,
        "stars": stars,
        "reviews": reviews,
        "updated_at": str(updated_at),
    }


def _detect_secret_harvest(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect secret collection and exfiltration patterns."""

    sensitive_hits = metadata["sensitive_inputs"] + _keyword_hits(corpus, _SENSITIVE_PATH_KEYWORDS)
    egress_hits = _keyword_hits(corpus, _NETWORK_EGRESS_KEYWORDS)
    env_hits = _keyword_hits(corpus, ["os.getenv", "process.env", "dotenv", "api_key", "access_token"])

    if not sensitive_hits or (not egress_hits and not env_hits):
        return None

    evidence = []
    if metadata["sensitive_inputs"]:
        evidence.append(f"声明了敏感输入参数: {', '.join(metadata['sensitive_inputs'][:4])}")
    if env_hits:
        evidence.append(f"代码中出现敏感配置读取迹象: {', '.join(env_hits[:4])}")
    if egress_hits:
        evidence.append(f"同时存在外发网络行为迹象: {', '.join(egress_hits[:4])}")

    return _finding(
        key="secret_harvest",
        title="密钥收割型",
        severity="critical",
        summary="插件涉及 API Key / Token / SSH / 凭证读取，并伴随疑似上传或外发行为。",
        evidence=evidence,
        advice="除非你已经完整审计源码并确认不会上传敏感数据，否则不要安装。",
    )


def _detect_sensitive_secret_input(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect skills that ask the user to provide secrets or credentials."""

    hits = metadata["sensitive_inputs"] or _keyword_hits(corpus, _SECRET_REQUEST_KEYWORDS)
    if not hits:
        return None

    evidence = []
    if metadata["sensitive_inputs"]:
        evidence.append(f"声明了需要用户输入的敏感参数: {', '.join(metadata['sensitive_inputs'][:4])}")
    else:
        evidence.append(f"描述/源码中命中密钥相关关键词: {', '.join(hits[:4])}")

    return _finding(
        key="sensitive_secret_input",
        title="要你输入密钥的 Skill",
        severity="medium",
        summary="该 skill 会要求你提供 API Key、Token、Secret 或其他敏感凭证，输入前必须确认它会把这些数据用于什么地方。",
        evidence=evidence,
        advice="除非来源可信且用途明确，否则不要输入真实生产密钥；优先使用最小权限测试密钥。",
    )


def _detect_command_execution_capability(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect skills that can execute shell or system commands."""

    tool_hits = _matching_tools(metadata["tools"], _COMMAND_EXECUTION_TOOLS)
    keyword_hits = _keyword_hits(corpus, _COMMAND_EXEC_KEYWORDS)
    if not tool_hits and not keyword_hits:
        return None

    evidence = []
    if tool_hits:
        evidence.append(f"声明了可执行命令的工具: {', '.join(tool_hits[:4])}")
    if keyword_hits:
        evidence.append(f"描述/源码中命中命令执行关键词: {', '.join(keyword_hits[:4])}")

    return _finding(
        key="command_execution_capability",
        title="能执行命令的 Skill",
        severity="high",
        summary="该 skill 具备执行系统命令的能力，相当于把本机终端输入权交给自动化流程。",
        evidence=evidence,
        advice="只在审批、白名单和隔离环境都到位时才考虑安装这类 skill。",
    )


def _detect_browser_control_capability(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect skills that can automate or control a browser."""

    tool_hits = _matching_tools(metadata["tools"], {"browser", "browser_cdp", "playwright", "selenium", "webdriver", "puppeteer"})
    keyword_hits = _keyword_hits(corpus, _BROWSER_CONTROL_KEYWORDS)
    if not tool_hits and not keyword_hits:
        return None

    evidence = []
    if tool_hits:
        evidence.append(f"声明了浏览器自动化相关工具: {', '.join(tool_hits[:4])}")
    if keyword_hits:
        evidence.append(f"描述/源码中命中浏览器控制关键词: {', '.join(keyword_hits[:4])}")

    return _finding(
        key="browser_control_capability",
        title="能控制浏览器的 Skill",
        severity="high",
        summary="该 skill 可能访问浏览器会话、Cookies、已登录账号和网页交互流程，风险远高于普通摘要类 skill。",
        evidence=evidence,
        advice="不要在承载微信、支付、网银或企业后台登录态的浏览器环境中直接放行此类 skill。",
    )


def _detect_file_access_capability(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect skills that can read or traverse the local file system."""

    tool_hits = _matching_tools(metadata["tools"], _FILE_ACCESS_TOOLS)
    keyword_hits = _keyword_hits(corpus, _FILE_ACCESS_KEYWORDS)
    if not tool_hits and not keyword_hits:
        return None

    evidence = []
    if tool_hits:
        evidence.append(f"声明了文件系统访问工具: {', '.join(tool_hits[:4])}")
    if keyword_hits:
        evidence.append(f"描述/源码中命中文件访问关键词: {', '.join(keyword_hits[:4])}")

    return _finding(
        key="file_access_capability",
        title="能读取文件的 Skill",
        severity="medium",
        summary="该 skill 可访问本地文件系统，可能接触 SSH 私钥、配置文件、数据库或其他敏感资料。",
        evidence=evidence,
        advice="安装前确认它的访问目录范围，并避免给它读取整盘文件或敏感目录的能力。",
    )


def _detect_network_request_capability(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect skills that can reach the network or send requests."""

    tool_hits = _matching_tools(metadata["tools"], _NETWORK_REQUEST_TOOLS)
    keyword_hits = _keyword_hits(corpus, _NETWORK_REQUEST_KEYWORDS)
    if not tool_hits and not keyword_hits:
        return None

    evidence = []
    if tool_hits:
        evidence.append(f"声明了网络请求工具: {', '.join(tool_hits[:4])}")
    if keyword_hits:
        evidence.append(f"描述/源码中命中联网关键词: {', '.join(keyword_hits[:4])}")

    return _finding(
        key="network_request_capability",
        title="能发起网络请求的 Skill",
        severity="medium",
        summary="该 skill 可以访问外网或其他内网设备，存在数据外传、内网探测和横向访问风险。",
        evidence=evidence,
        advice="优先限制它可访问的域名和网段，不要默认让它访问整个内网或公网。",
    )


def _detect_scheduled_execution_capability(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect skills that can set schedules or keep running in background."""

    keyword_hits = _keyword_hits(corpus, _SCHEDULER_KEYWORDS)
    if not metadata["trigger_signals"] and not keyword_hits:
        return None

    evidence = []
    if metadata["trigger_signals"]:
        evidence.append(f"声明了定时/后台触发能力: {', '.join(metadata['trigger_signals'][:4])}")
    if keyword_hits:
        evidence.append(f"描述/源码中命中定时任务关键词: {', '.join(keyword_hits[:4])}")

    return _finding(
        key="scheduled_execution_capability",
        title="能设置定时任务的 Skill",
        severity="high",
        summary="该 skill 可能会在后台持续或定时运行，即使你不主动触发，也可能继续执行任务。",
        evidence=evidence,
        advice="除非你能确认它的触发条件、停止方式和清理逻辑，否则不要安装或长期启用。",
    )


def _detect_miner(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect miner or cryptojacking traits."""

    hits = _keyword_hits(corpus, _MINER_KEYWORDS)
    if not hits:
        return None

    return _finding(
        key="miner_injection",
        title="挖矿注入型",
        severity="critical",
        summary="插件内容中出现挖矿程序、矿池或加密货币挖矿关键词。",
        evidence=[f"命中关键词: {', '.join(hits[:5])}"],
        advice="建议直接取消安装，并避免在生产或个人主机上运行该 skill。",
    )


def _detect_dynamic_pull(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect runtime remote fetch and execution behavior."""

    exec_hits = _keyword_hits(corpus, _REMOTE_EXEC_KEYWORDS)
    has_remote_url = bool(_URL_PATTERN.search(corpus))
    if not exec_hits and not (has_remote_url and "http_get" in metadata["tools"]):
        return None

    evidence = []
    if exec_hits:
        evidence.append(f"发现运行期远程拉取/执行关键词: {', '.join(exec_hits[:5])}")
    if has_remote_url:
        evidence.append("源码或元数据中包含远程 URL，且存在动态执行迹象。")

    return _finding(
        key="dynamic_pull",
        title="动态拉取型",
        severity="high",
        summary="插件疑似在运行期从外部站点下载脚本、配置或恶意逻辑。",
        evidence=evidence,
        advice="建议先离线审查源码；如果用途不明确，不要安装。",
    )


def _detect_overprivilege(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect privilege requests that do not match the stated purpose."""

    dangerous_tools = [tool for tool in metadata["tools"] if tool in _DANGEROUS_TOOLS]
    benign = any(keyword in metadata["description"].lower() or keyword in metadata["name"].lower() for keyword in _BENIGN_USE_CASE_KEYWORDS)
    sensitive_path_hits = _keyword_hits(corpus, _SENSITIVE_PATH_KEYWORDS)

    if not dangerous_tools and not sensitive_path_hits:
        return None
    if not benign and not sensitive_path_hits:
        return None

    evidence = []
    if benign and dangerous_tools:
        evidence.append(f"功能描述偏轻量，但工具权限较重: {', '.join(dangerous_tools[:4])}")
    if sensitive_path_hits:
        evidence.append(f"代码中出现敏感路径/文件访问迹象: {', '.join(sensitive_path_hits[:4])}")

    return _finding(
        key="overprivilege",
        title="越权访问型",
        severity="high",
        summary="插件声明的用途与其申请的工具权限或敏感文件访问范围不匹配。",
        evidence=evidence,
        advice="检查它是否真的需要这些权限；若权限明显超出用途，建议取消安装。",
    )


def _detect_impersonation(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Detect names that imitate official or famous skills."""

    name = metadata["name"].lower()
    hits = [keyword for keyword in _IMPERSONATION_KEYWORDS if keyword in name]
    looks_like_clone = any(token in name for token in ["-pro", "-plus", "official", "premium"])
    if not hits and not looks_like_clone:
        return None
    if metadata["author"] or metadata["homepage"] or metadata["repository"]:
        return None

    evidence = [f"名称包含高仿官方/知名插件特征: {', '.join((hits or ['-pro/-plus'])[:4])}"]
    return _finding(
        key="impersonation",
        title="仿冒官方型",
        severity="medium",
        summary="插件命名存在模仿官方或知名 skill 的倾向，但缺少可信来源信息。",
        evidence=evidence,
        advice="确认作者、主页和仓库真实性后再决定是否安装。",
    )


def _detect_missing_provenance(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Detect missing provenance and authorship."""

    if metadata["author"] or metadata["homepage"] or metadata["repository"]:
        return None

    evidence = ["未发现作者、主页或仓库来源信息。"]
    if not metadata["documentation_present"]:
        evidence.append("同时缺少 README / SKILL.md 说明。")

    return _finding(
        key="missing_provenance",
        title="无作者溯源型",
        severity="medium",
        summary="插件缺少作者、主页、仓库等基础溯源信息。",
        evidence=evidence,
        advice="来源不明的 skill 默认不推荐安装。",
    )


def _detect_third_party_content(metadata: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    """Detect third-party content ingestion behavior."""

    hits = _keyword_hits(corpus, _REMOTE_CONTENT_KEYWORDS)
    if not hits:
        return None

    return _finding(
        key="third_party_content",
        title="第三方内容抓取型",
        severity="medium",
        summary="插件会抓取网页、远端接口或第三方内容，可能把不可信文本带入执行流程。",
        evidence=[f"命中远端内容抓取关键词: {', '.join(hits[:5])}"],
        advice="如果确实要安装，建议只在隔离环境中使用，并增加提示注入防护。",
    )


def _detect_unmaintained(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Detect missing ratings or maintenance information."""

    if metadata["downloads"] is None and metadata["stars"] is None and metadata["reviews"] is None and metadata["updated_at"]:
        return None
    if metadata["downloads"] not in (None, 0) or metadata["stars"] not in (None, 0) or metadata["reviews"] not in (None, 0):
        return None

    evidence = []
    if metadata["downloads"] in (None, 0):
        evidence.append("下载量信息缺失或极低。")
    if metadata["stars"] in (None, 0):
        evidence.append("评分 / Star 信息缺失或为 0。")
    if not metadata["updated_at"]:
        evidence.append("缺少最近更新时间。")

    return _finding(
        key="unmaintained",
        title="无评分无维护型",
        severity="low",
        summary="插件缺少下载、评分或更新时间等维护信号，后续维护风险较高。",
        evidence=evidence,
        advice="建议优先选择维护活跃、评价明确的 skill。",
    )


def _build_corpus(detail: Dict[str, Any], bundle: Dict[str, Any]) -> str:
    """Build a normalized text corpus for keyword inspection."""

    parts = [
        json.dumps(detail, ensure_ascii=False, sort_keys=True),
        json.dumps(bundle, ensure_ascii=False, sort_keys=True),
    ]
    files = bundle.get("files", {})
    if isinstance(files, dict):
        for file_name, file_content in files.items():
            parts.append(str(file_name))
            parts.append(str(file_content))
    return "\n".join(parts).lower()


def _collect_tool_names(data: Dict[str, Any]) -> List[str]:
    """Collect declared tool names from a skill payload."""

    tools = []
    declared_tools = data.get("tools", [])
    if isinstance(declared_tools, list):
        tools.extend(str(tool).strip() for tool in declared_tools if str(tool).strip())
    actions = data.get("actions", [])
    if isinstance(actions, list):
        tools.extend(_collect_tools_from_actions(actions))
    return [tool for tool in tools if tool]


def _collect_tools_from_actions(actions: Iterable[Dict[str, Any]]) -> List[str]:
    """Collect tool names recursively from actions."""

    tools: List[str] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        tool_name = str(action.get("tool") or "").strip()
        if tool_name:
            tools.append(tool_name)
        nested = action.get("actions")
        if isinstance(nested, list):
            tools.extend(_collect_tools_from_actions(nested))
        then_nested = action.get("then")
        if isinstance(then_nested, list):
            tools.extend(_collect_tools_from_actions(then_nested))
    return tools


def _collect_input_names(data: Dict[str, Any]) -> List[str]:
    """Collect declared input names from a skill payload."""

    inputs = data.get("inputs", {})
    if isinstance(inputs, dict):
        return [str(name).strip() for name in inputs.keys() if str(name).strip()]
    return []


def _collect_trigger_signals(data: Dict[str, Any]) -> List[str]:
    """Collect schedule/background trigger markers from a skill payload."""

    signals: List[str] = []
    triggers = data.get("triggers", [])
    if not isinstance(triggers, list):
        return signals

    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        trigger_type = str(trigger.get("type") or "").strip()
        schedule = str(trigger.get("schedule") or "").strip()
        event = str(trigger.get("event") or "").strip()
        if trigger_type:
            signals.append(trigger_type)
        if schedule:
            signals.append(schedule)
        if event:
            signals.append(event)
    return [signal for signal in signals if signal]


def _keyword_hits(text: str, keywords: Iterable[str]) -> List[str]:
    """Return keywords present in text while preserving order."""

    hits = [keyword for keyword in keywords if keyword.lower() in text]
    return _unique(hits)


def _matching_tools(tool_names: Iterable[str], markers: Iterable[str]) -> List[str]:
    """Return matching tool names using exact or contains checks."""

    normalized_markers = [marker.lower() for marker in markers]
    hits: List[str] = []
    for tool_name in tool_names:
        lowered = str(tool_name).lower()
        if any(lowered == marker or marker in lowered for marker in normalized_markers):
            hits.append(str(tool_name))
    return _unique(hits)


def _finding(
    key: str,
    title: str,
    severity: str,
    summary: str,
    evidence: List[str],
    advice: str,
) -> Dict[str, Any]:
    """Create a normalized finding payload."""

    return {
        "key": key,
        "title": title,
        "severity": severity,
        "severity_label": _RISK_LABELS[severity],
        "score": _SEVERITY_WEIGHT[severity],
        "summary": summary,
        "evidence": _unique([item for item in evidence if item]),
        "advice": advice,
    }


def _first_nonempty(*values: Any) -> Any:
    """Return the first meaningful value."""

    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (list, dict)) and value:
            return value
        if isinstance(value, (int, float)):
            return value
    return None


def _coerce_stat(value: Any) -> Optional[int]:
    """Coerce a numeric stat if possible."""

    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _unique(items: Iterable[str]) -> List[str]:
    """Return unique items while preserving order."""

    seen = set()
    ordered: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
