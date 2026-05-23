# Public Release Compliance Checklist

This project has been prepared for a public GitHub repository with the
following baseline controls:

- Root `LICENSE` file added for MIT licensing.
- Third-party dependency and adapted-material notes added in
  `THIRD_PARTY_NOTICES.md`.
- Trademark and affiliation disclaimer added in `NOTICE`.
- Local tool configuration directories are ignored by `.gitignore`.
- Known copied/reference-only drafts with unclear redistribution terms were
  removed from the tracked project content.
- Synthetic invoice fixtures are documented as test-only sample data.
- Local machine paths removed from `OPENCLAW_LOCAL_REFERENCE.md`; all
  references now point to public OpenClaw documentation URLs.
- Personal desktop folder references removed from `PROGRESS.md`.
- MIT upstream attribution comment added to
  `bundled_skills/web-access/scripts/cdp-proxy.mjs`.
- Internal development documents (`CODE_REVIEW.md`, `DEV_PROGRESS*.md`,
  `PLAN.md`) removed from git tracking and added to `.gitignore`.

## Pre-Push Checks

Run these checks before pushing:

```bash
git status --short
git grep -n -I -E "(sk-[A-Za-z0-9]|ghp_|github_pat_|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,}|xox[baprs]-)"
python -m pytest
```

If a real secret was ever committed in previous history, remove it from the
Git history and rotate the secret before publishing the repository.

## Remaining Human Review Items

- Confirm the public copyright holder name you want in `LICENSE`.
- Re-check upstream terms for the adapted `web-access` bundle before a tagged
  release or package distribution.
- Confirm whether optional LGPL dependency `python-telegram-bot` is acceptable
  for your distribution model if you enable the `telegram` extra.
- Confirm that references to OpenClaw are descriptive compatibility references,
  not branding or endorsement claims.
