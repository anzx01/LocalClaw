---
name: self-improving-agent
version: 1.0.0
description: Turn goals, weaknesses, and observations into a concrete self-improvement plan.
type: workflow
inputs:
  goal: string
  current_state: string
  constraints: string
tools:
  - _local_model_prompt
actions:
  - type: tool_call
    name: improvement_plan
    tool: _local_model_prompt
    params:
      prompt: >-
        You are generating a self-improvement plan for an agent or workflow.
        Goal: {{goal}}
        Current state: {{current_state}}
        Constraints: {{constraints or 'none provided'}}

        Return:
        1. the top bottlenecks
        2. concrete improvements
        3. measurable success criteria
        4. the safest next experiment
      max_tokens: 900
      temperature: 0.3
permissions:
  risk_level: low
metadata:
  display_name: Self-Improving-Agent
  author: LocalClaw
  category: planning
  tags:
    - openclaw
    - planning
    - improvement
    - local-model
  catalog_id: self-improving-agent
  skill_key: self-improving-agent
  aliases:
    - Self-Improving-Agent
    - self-improve
  openclaw:
    skillKey: self-improving-agent
    aliases:
      - Self-Improving-Agent
      - self-improve
---

# Self-Improving-Agent

Use this skill to turn retrospective notes or capability gaps into a concrete
improvement plan backed by the local model.
