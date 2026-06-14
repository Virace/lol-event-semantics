# AGENTS.md

This repository stores League of Legends audio event semantics data, rules, and tooling.

## Scope

- This file applies to the repository root and all child paths.

## Working Rules

- Use `uv` for environment management, dependency sync, and command execution.
- Keep `raw_event_name` as immutable source data. Any readable label or translation must be stored as derived data.
- Prefer generated artifacts in `dist/` over runtime-only dynamic text assembly when the output is meant to be stable and user-facing.
- Treat `rules/` and `overrides/` as long-lived source-of-truth inputs. Do not manually edit generated files when the same change belongs in source inputs.
- Do not commit large temporary datasets, caches, scratch outputs, or one-off exports. Keep local experiments under ignored paths.
- Keep scripts in `scripts/` thin and task-oriented. Shared logic belongs in `src/lol_event_semantics/`.
- Keep README and directory notes aligned with the current repository layout whenever the layout changes.

## Repository Layout

- `src/lol_event_semantics/`: shared Python package code.
- `scripts/`: repository entry scripts for import, compile, validate, and maintenance tasks.
- `data/`: checked-in small source datasets, schemas, and curated samples.
- `rules/`: reusable normalization rules, dictionaries, and templates.
- `overrides/`: manually curated exact overrides and review decisions.
- `dist/`: generated release artifacts intended for downstream consumption.

## Python Style

- Prefer small, focused modules and concise names.
- Every function must include a Google-style docstring.
- Write docstrings and code comments primarily in Chinese.
- Add concise comments for logic that is complex, hard to read, constant-heavy, or branch-heavy.
- Comments should explain intent, constraints, or why a decision exists, not restate obvious code line by line.
- Do not add placeholder abstractions or speculative framework code before the first real workflow needs them.

## Verification

- Before claiming scaffold work is complete, verify at least:
  - package import works in the project environment
  - ignored local environment paths remain ignored by git
  - `git status --short` matches the expected new files
