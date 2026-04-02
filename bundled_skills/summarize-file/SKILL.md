---
name: summarize-file
version: 1.0.0
description: Read a file and summarize its content using the local model
type: workflow
inputs:
  path: string
  file: string
  length: string
  focus: string
tools:
  - file_read
  - _local_model_prompt
actions:
  - type: tool_call
    name: read_file
    tool: file_read
    params:
      path: "{{path or file}}"
  
  - type: tool_call
    name: summarize_content
    tool: _local_model_prompt
    params:
      prompt: >-
        请用中文简洁地总结以下文件内容。
        文件路径: {{path or file}}
        总结长度: {{length or '简短'}}
        关注重点: {{focus or '关键信息和要点'}}

        文件内容（如内容过长已截取前2000字）:
        {{(read_file.content or '')[:2000]}}
      max_tokens: 512
      temperature: 0.2
    depends_on:
      - read_file
permissions:
  risk_level: low
metadata:
  display_name: Summarize File
  author: LocalClaw
  category: productivity
  tags:
    - openclaw
    - summarize
    - file
    - local-model
  catalog_id: summarize-file
  skill_key: summarize-file
  aliases:
    - summarize-file
    - file-summary
  openclaw:
    skillKey: summarize-file
    aliases:
      - summarize-file
      - file-summary
---

# Summarize File

OpenClaw-style workflow skill that combines file reading and summarization.

When the user asks to summarize a file (e.g., "总结我桌面的 xxx.txt"), this skill:
1. Reads the file content using `file_read` tool
2. Passes the content to the local model via `_local_model_prompt` for summarization

Accepts both `path` and `file` parameter keys for compatibility with different model outputs.
