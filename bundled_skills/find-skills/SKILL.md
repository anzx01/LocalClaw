---
name: find-skills
version: 1.0.0
description: Search bundled skills and ClawHub skills that match a user need.
type: workflow
inputs:
  query: string
tools:
  - clawhub_search
actions:
  - type: tool_call
    name: search_skills
    tool: clawhub_search
    params:
      query: "{{query}}"
permissions:
  risk_level: low
metadata:
  display_name: Find-Skills
  author: LocalClaw
  category: marketplace
  tags:
    - openclaw
    - clawhub
    - discovery
    - marketplace
  catalog_id: find-skills
  skill_key: find-skills
  aliases:
    - Find-Skills
    - skill-finder
  openclaw:
    skillKey: find-skills
    aliases:
      - Find-Skills
      - skill-finder
---

# Find-Skills

Use this skill when the user asks which skill should be installed for a task, or
when they want to browse the LocalClaw and ClawHub catalog.

Keep `query` short and capability-focused.
