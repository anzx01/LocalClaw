---
name: weather.forecast
version: 1.0.0
description: OpenClaw-style weather forecast skill powered by wttr.in for current conditions and short forecasts.
type: workflow
inputs:
  location: string
tools:
  - http_get
actions:
  - type: tool_call
    name: fetch_weather
    tool: http_get
    params:
      url: https://wttr.in/{{location}}?format=j1
    timeout: 10
permissions:
  risk_level: low
metadata:
  author: LocalClaw
  homepage: https://github.com/openclaw/openclaw
  repository: https://github.com/openclaw/openclaw
  category: weather
  tags:
    - openclaw
    - weather
    - forecast
  catalog_id: weather.forecast
  skill_key: weather.forecast
  aliases:
    - forecast
    - weather
  openclaw:
    skillKey: weather.forecast
    aliases:
      - forecast
      - weather
    homepage: https://github.com/openclaw/openclaw
---

# Weather Forecast

Use this skill for real-time weather or forecast questions.

It should be chosen instead of answering from memory whenever the user asks about:
- current weather
- rain / snow / temperature
- today / tomorrow / future forecast

Provide `location` from the user request when possible.
