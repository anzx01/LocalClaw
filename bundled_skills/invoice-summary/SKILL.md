---
name: invoice-summary
version: 1.0.0
description: Summarize invoice amounts and purposes from PDF files in directories
type: workflow
inputs:
  directories: array
  dir1: string
  dir2: string
triggers:
  - type: intent
    pattern: "invoice_summary|summarize_invoices|汇总发票|统计发票"
  - type: keyword
    keywords:
      - "发票"
      - "汇总"
      - "invoice"
tools:
  - pdf_extract
  - _local_model_prompt
actions:
  - type: tool_call
    name: extract_dir1_pdfs
    tool: pdf_extract
    params:
      path: "{{dir1}}"
      max_chars_per_file: 2000

  - type: tool_call
    name: extract_dir2_pdfs
    tool: pdf_extract
    params:
      path: "{{dir2}}"
      max_chars_per_file: 2000

  - type: tool_call
    name: summarize_invoices
    tool: _local_model_prompt
    params:
      prompt: >-
        请从以下PDF发票内容中提取并汇总：
        1. 每张发票的金额
        2. 每张发票的用途（住宿/交通/餐饮等）
        3. 按目录分组统计总额

        PDF内容（目录1）：
        {{extract_dir1_pdfs.content}}

        PDF内容（目录2）：
        {{extract_dir2_pdfs.content}}

        请用表格形式输出，包含：发票文件名、金额、用途、所属目录。
        最后补充每个目录的合计金额和总金额。
      max_tokens: 1024
      temperature: 0.1
    depends_on:
      - extract_dir1_pdfs
      - extract_dir2_pdfs
permissions:
  risk_level: low
metadata:
  display_name: Invoice Summary
  author: LocalClaw
  category: finance
  tags:
    - invoice
    - pdf
    - summary
  catalog_id: invoice-summary
  skill_key: invoice-summary
---

# Invoice Summary

汇总多个目录下的PDF发票金额和用途。

使用示例：
- "汇总桌面北京出差发票和长沙出差发票"
- "统计这两个文件夹的发票"
