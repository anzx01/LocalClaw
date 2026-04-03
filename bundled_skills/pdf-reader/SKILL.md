---
name: pdf-reader
version: 1.0.0
description: Extract text content from PDF files
type: workflow
inputs:
  path: string
  file: string
tools:
  - shell
actions:
  - type: tool_call
    name: extract_pdf
    tool: shell
    params:
      command: >-
        python -c "import sys; from pathlib import Path; 
        try: import pypdf; reader = pypdf.PdfReader(r'{{path or file}}'); 
        text = '\\n'.join(page.extract_text() for page in reader.pages); 
        print(text if text.strip() else 'PDF无文本内容')
        except Exception as e: print(f'错误: {e}', file=sys.stderr); sys.exit(1)"
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
