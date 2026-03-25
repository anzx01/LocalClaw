---
name: web-access
version: "2.4.0-localclaw.1"
license: MIT
github: https://github.com/eze-is/web-access
description: LocalClaw-native adaptation of web-access for real web tasks, search, logged-in browsing, dynamic pages, and browser interaction through the local Chrome session.
type: workflow
inputs:
  request: string
  url: string
tools:
  - browser_cdp
actions:
  - type: tool_call
    name: run_web_access
    tool: browser_cdp
    params:
      request: "{{request}}"
      url: "{{url}}"
permissions:
  risk_level: high
requires:
  bins:
    - node
metadata:
  author: 一泽 Eze
  adapter: LocalClaw
  homepage: https://github.com/eze-is/web-access
  repository: https://github.com/eze-is/web-access
  category: web
  tags:
    - browser
    - cdp
    - web
    - openclaw
    - localclaw
  catalog_id: web-access
  skill_key: web-access
  aliases:
    - real-browser
    - browser-cdp
    - dynamic-web
  openclaw:
    skillKey: web-access
    aliases:
      - real-browser
      - browser-cdp
      - dynamic-web
    homepage: https://github.com/eze-is/web-access
---

# Web Access

This is a LocalClaw-native adaptation of the upstream `web-access` skill.

What stayed the same:
- goal-directed web work instead of fixed scripts
- evidence-first browsing: inspect, adapt, then continue
- prefer the cheapest effective path first
- switch to the real browser when login state, dynamic rendering, search navigation, or anti-bot-sensitive access is needed

What changed for LocalClaw:
- Claude Code specific slash commands are replaced by the native `browser_cdp` tool
- proxy startup and Chrome readiness are handled for the current machine environment
- the tool operates only on background tabs it creates itself and closes them when done
- this runtime is text-first, so DOM extraction is preferred over screenshot-based reasoning

Use this skill when the user asks for:
- latest or live web information that should not be answered from memory
- browsing a known URL that may require richer extraction than a plain HTTP fetch
- interacting with a site, using logged-in Chrome state, or reading dynamic pages
- web search tasks that need a real browser session

Operational notes:
- `scripts/check-deps.py` can be run manually to verify Node.js, proxy readiness, and Chrome remote debugging
- site-specific notes may be stored under `references/site-patterns/`
- `references/cdp-api.md` documents the low-level proxy API that the LocalClaw tool wraps
