---
name: tavily-web-search
version: 1.0.0
description: Search the web with Tavily when you need fresher or more structured results.
type: workflow
inputs:
  query: string
  search_depth: string
  max_results: string
requires:
  env:
    - TAVILY_API_KEY
  bins:
    - python
tools:
  - safe_shell
actions:
  - type: tool_call
    name: tavily_search
    tool: safe_shell
    params:
      command: >-
        python -c "import json, os, urllib.request; payload={'query': os.environ['LOCALCLAW_QUERY'], 'search_depth': os.environ.get('LOCALCLAW_SEARCH_DEPTH') or 'basic', 'max_results': int(os.environ.get('LOCALCLAW_MAX_RESULTS') or '5'), 'include_answer': True}; req=urllib.request.Request('https://api.tavily.com/search', data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['TAVILY_API_KEY']}); print(urllib.request.urlopen(req, timeout=30).read().decode('utf-8'))"
      env:
        LOCALCLAW_QUERY: "{{query}}"
        LOCALCLAW_SEARCH_DEPTH: "{{search_depth}}"
        LOCALCLAW_MAX_RESULTS: "{{max_results}}"
permissions:
  risk_level: low
metadata:
  display_name: Tavily Web Search
  author: LocalClaw
  category: web-search
  tags:
    - openclaw
    - tavily
    - web
    - search
  catalog_id: tavily-web-search
  skill_key: tavily-web-search
  aliases:
    - Tavily Web Search
    - tavily
    - tavily-search
  openclaw:
    skillKey: tavily-web-search
    aliases:
      - Tavily Web Search
      - tavily
      - tavily-search
---

# Tavily Web Search

Use this skill when you want Tavily-backed search instead of the basic HTTP
fetch flow.

Requirements:
- `TAVILY_API_KEY` must be configured
- Python must be available in PATH
