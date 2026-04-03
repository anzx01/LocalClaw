---
name: pdf-reader
version: 1.0.0
description: Extract text content from PDF files
type: workflow
inputs:
  path: string
  file: string
tools:
  - pdf_extract
actions:
  - type: tool_call
    name: extract_pdf
    tool: pdf_extract
    params:
      path: "{{path or file}}"
permissions:
  risk_level: low
metadata:
  display_name: PDF Reader
  author: LocalClaw
  category: file
  tags:
    - pdf
    - file
    - extract
  catalog_id: pdf-reader
  skill_key: pdf-reader
---
