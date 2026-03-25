---
name: agent-browser
version: 1.0.0
description: Fetch a page or endpoint over HTTP when a lightweight browser-style read is enough.
type: workflow
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
permissions:
  risk_level: low
metadata:
  display_name: Agent Browser
  author: LocalClaw
  category: web
  tags:
    - openclaw
    - browser
    - web
    - fetch
  catalog_id: agent-browser
  skill_key: agent-browser
  aliases:
    - Agent Browser
    - browser
    - browse-url
  openclaw:
    skillKey: agent-browser
    aliases:
      - Agent Browser
      - browser
      - browse-url
---

# Agent Browser

Use this skill to fetch the contents of a URL with LocalClaw's built-in HTTP
tooling.

This is the LocalClaw-compatible lightweight browser variant. It is best for:
- reading JSON or HTML from a page
- checking a docs page or API endpoint
- fetching a URL before you summarize or inspect it
