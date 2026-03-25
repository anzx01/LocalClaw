---
name: skill-vetter
version: 1.0.0
description: Review a ClawHub skill for installation risk before you install it.
type: workflow
inputs:
  skill_id: string
tools:
  - clawhub_scan
actions:
  - type: tool_call
    name: scan_skill
    tool: clawhub_scan
    params:
      skill_id: "{{skill_id}}"
permissions:
  risk_level: low
metadata:
  display_name: Skill-Vetter
  author: LocalClaw
  category: security
  tags:
    - openclaw
    - clawhub
    - security
    - review
  catalog_id: skill-vetter
  skill_key: skill-vetter
  aliases:
    - Skill-Vetter
    - vet-skill
  openclaw:
    skillKey: skill-vetter
    aliases:
      - Skill-Vetter
      - vet-skill
---

# Skill-Vetter

Use this skill when you want a LocalClaw security review for a marketplace skill
before installation.

Provide the ClawHub slug in `skill_id`.
