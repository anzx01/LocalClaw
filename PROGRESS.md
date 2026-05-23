# Progress

Updated: 2026-04-03 17:03:17

## Done

- Added a low-risk `pdf_extract` tool for PDF text extraction.
- Switched `invoice-summary` and `pdf-reader` to use `pdf_extract` instead of `shell`.
- Added `pypdf>=4.2` to `requirements.txt` and `pyproject.toml`.
- Added `tests/test_invoice_summary_skill.py`.
- Verified these tests pass:
  - `python -m pytest tests/test_invoice_summary_skill.py tests/test_file_tool.py -q`
  - `python -m pytest tests/test_skills.py tests/test_skill_registry.py -q`
  - `python -m pytest tests/test_engine.py -k internal_local_model_prompt_skill -q`

## Current Blocker

- The user wants invoice summarization to stay on the local-model path.
- The current failure is not PDF extraction. The current failure is `_local_model_prompt` timing out.
- `.env` is set to `qwen3:4b`, but runtime falls back to installed `gemma3:4b`.
- Test directories: two invoice directories with a mix of hotel, meal, and taxi PDFs.
- Extracted PDF text size is roughly 1200–1400 chars per directory.
- A reduced local-model request with `max_tokens=256` still timed out under the current setup.

## Important Constraint

- Do not replace invoice summarization with a rule-based summary tool.
- Continue on the local-model summarization path.

## Next Steps

1. Investigate local-model timeout behavior first.
2. Check whether per-call timeout support is needed.
3. Reduce the `invoice-summary` prompt further if possible.
4. Lower `max_tokens` if needed.
5. Consider switching to a faster local model.
6. Re-test with the invoice test fixture directories.

## Current Uncommitted Files

- `bundled_skills/invoice-summary/SKILL.md`
- `bundled_skills/pdf-reader/SKILL.md`
- `localclaw/tools/file_tool.py`
- `pyproject.toml`
- `requirements.txt`
- `tests/test_invoice_summary_skill.py`
