---
name: weather
version: 1.0.0
description: Get current weather and short forecasts from wttr.in for any city or region.
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
      url: "https://wttr.in/{{location}}?format=j1"
permissions:
  risk_level: low
metadata:
  display_name: Weather
  author: LocalClaw
  homepage: https://wttr.in/:help
  category: weather
  tags:
    - openclaw
    - weather
    - forecast
  catalog_id: weather
  skill_key: weather
  aliases:
    - Weather
    - forecast
  openclaw:
    skillKey: weather
    aliases:
      - Weather
      - forecast
    homepage: https://wttr.in/:help
---

# Weather

Use this skill for current weather, rain checks, and short forecasts.

Always include a city, region, or other concrete location in `location`.
