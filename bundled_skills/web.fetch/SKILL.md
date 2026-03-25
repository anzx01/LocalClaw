---
name: web.fetch
version: 1.0.0
description: OpenClaw-style web fetch skill for reading a URL over HTTP from inside LocalClaw.
type: atomic
inputs:
  url: string
tools:
  - http_get
actions:
  - type: tool_call
    name: fetch_url
    tool: http_get
    params:
      url: "{{url}}"
    timeout: 15
permissions:
  risk_level: low
metadata:
  author: LocalClaw
  homepage: https://github.com/openclaw/openclaw
  repository: https://github.com/openclaw/openclaw
  category: web
  tags:
    - openclaw
    - web
    - http
  catalog_id: web.fetch
  skill_key: web.fetch
  aliases:
    - fetch-url
    - http-read
  openclaw:
    skillKey: web.fetch
    aliases:
      - fetch-url
      - http-read
    homepage: https://github.com/openclaw/openclaw
---

# Web Fetch

Use this skill when the user wants LocalClaw to retrieve the contents of a URL.

It is appropriate for simple HTTP reads and page fetches where a single URL is enough.
