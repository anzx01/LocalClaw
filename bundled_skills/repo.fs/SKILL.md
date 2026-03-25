---
name: repo.fs
version: 1.0.0
description: OpenClaw-style workspace file skill for listing, reading, writing, appending, deleting, and creating directories.
type: workflow
inputs:
  action: string
  path: string
  content: string
  mode: string
  recursive: boolean
  parents: boolean
  all: boolean
  long: boolean
tools:
  - file_list
  - file_read
  - file_write
  - file_append
  - file_delete
  - file_mkdir
actions:
  - type: tool_call
    name: list_workspace
    tool: file_list
    condition: "{{action == 'list'}}"
    params:
      path: "{{path}}"
      all: "{{all}}"
      long: "{{long}}"
  - type: tool_call
    name: read_file
    tool: file_read
    condition: "{{action == 'read'}}"
    params:
      path: "{{path}}"
  - type: tool_call
    name: write_file
    tool: file_write
    condition: "{{action == 'write'}}"
    params:
      path: "{{path}}"
      content: "{{content}}"
      mode: "{{mode}}"
  - type: tool_call
    name: append_file
    tool: file_append
    condition: "{{action == 'append'}}"
    params:
      path: "{{path}}"
      content: "{{content}}"
  - type: tool_call
    name: delete_path
    tool: file_delete
    condition: "{{action == 'delete'}}"
    params:
      path: "{{path}}"
      recursive: "{{recursive}}"
  - type: tool_call
    name: make_directory
    tool: file_mkdir
    condition: "{{action == 'mkdir'}}"
    params:
      path: "{{path}}"
      parents: "{{parents}}"
permissions:
  risk_level: low
metadata:
  author: LocalClaw
  homepage: https://github.com/openclaw/openclaw
  repository: https://github.com/openclaw/openclaw
  category: workspace
  tags:
    - openclaw
    - workspace
    - files
  catalog_id: repo.fs
  skill_key: repo.fs
  aliases:
    - workspace-files
    - fs-workspace
  openclaw:
    skillKey: repo.fs
    aliases:
      - workspace-files
      - fs-workspace
    homepage: https://github.com/openclaw/openclaw
---

# Repo FS

Use this skill when the user needs to interact with files inside the current workspace.

Supported actions:
- `list`: list files or directories
- `read`: read a file
- `write`: overwrite or create a file
- `append`: append content to an existing file
- `delete`: remove a file or directory
- `mkdir`: create a directory

Prefer this skill over raw shell commands for normal workspace file operations.
