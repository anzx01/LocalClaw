---
name: humanizer
version: 1.0.0
description: Rewrite text so it sounds more natural, direct, and human.
type: workflow
inputs:
  text: string
  tone: string
  audience: string
tools:
  - _local_model_prompt
actions:
  - type: tool_call
    name: humanize_text
    tool: _local_model_prompt
    params:
      prompt: >-
        Rewrite the following text so it sounds more natural and human while
        preserving meaning.
        Tone: {{tone or 'natural and confident'}}.
        Audience: {{audience or 'general'}}.

        Text:
        {{text}}
      max_tokens: 900
      temperature: 0.5
permissions:
  risk_level: low
metadata:
  display_name: Humanizer
  author: LocalClaw
  category: writing
  tags:
    - openclaw
    - writing
    - rewrite
    - local-model
  catalog_id: humanizer
  skill_key: humanizer
  aliases:
    - Humanizer
    - humanize
  openclaw:
    skillKey: humanizer
    aliases:
      - Humanizer
      - humanize
---

# Humanizer

Use this skill when the user wants a draft rewritten in a more natural,
less robotic style while keeping the original meaning.
