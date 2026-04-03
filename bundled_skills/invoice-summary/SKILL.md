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
  - file_list
  - shell
  - _local_model_prompt
actions:
  - type: tool_call
    name: list_dir1
    tool: file_list
    params:
      path: "{{dir1}}"
  
  - type: tool_call
    name: list_dir2
    tool: file_list
    params:
      path: "{{dir2}}"
    
  - type: tool_call
    name: extract_all_pdfs
    tool: shell
    params:
      command: >-
        python -c "
        import sys
        from pathlib import Path
        try:
            import pypdf
        except ImportError:
            print('需要安装 pypdf: pip install pypdf')
            sys.exit(1)
        
        dirs = ['{{dir1}}', '{{dir2}}']
        results = []
        
        for dir_path in dirs:
            p = Path(dir_path)
            if not p.exists():
                continue
            dir_name = p.name
            for pdf_file in p.glob('*.pdf'):
                try:
                    reader = pypdf.PdfReader(str(pdf_file))
                    text = ''.join(page.extract_text() for page in reader.pages)
                    results.append(f'### {dir_name}/{pdf_file.name}')
                    results.append(text[:2000])
                    results.append('')
                except Exception as e:
                    results.append(f'### {dir_name}/{pdf_file.name} - 错误: {e}')
        
        print('\\n'.join(results))
        "
    depends_on:
      - list_dir1
      - list_dir2
  
  - type: tool_call
    name: summarize_invoices
    tool: _local_model_prompt
    params:
      prompt: >-
        请从以下PDF发票内容中提取并汇总：
        1. 每张发票的金额
        2. 每张发票的用途（住宿/交通/餐饮等）
        3. 按目录分组统计总额
        
        PDF内容：
        {{extract_all_pdfs.output}}
        
        请用表格形式输出，包含：发票文件名、金额、用途、所属目录
      max_tokens: 1024
      temperature: 0.1
    depends_on:
      - extract_all_pdfs
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
