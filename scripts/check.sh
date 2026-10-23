#!/usr/bin/env bash
# Every quality gate of every package in the workspace, the same ones CI runs.
# Usage: ./scripts/check.sh   (after `uv sync --locked --all-extras` in this folder)
set -euo pipefail
cd "$(dirname "$0")/.."
uv lock --check   # the one uv.lock matches every package's pyproject.toml
uv run --frozen python scripts/check_links.py   # relative links in every Markdown file
bash -n serving/serve.sh   # the model-server script parses
bash -n deploy/research-api.sh   # the shared-server script parses
if command -v shellcheck > /dev/null 2>&1; then
  shellcheck deploy/research-api.sh   # and passes shellcheck, where it is installed
else
  echo "== shellcheck skipped: not on PATH"
fi
for script in apps/web/scripts/*.sh; do bash -n "$script"; done   # the web gate scripts parse
for check in packages/*/scripts/check.sh; do
  echo "== ${check%/scripts/check.sh}"
  "$check"
done
if command -v pnpm > /dev/null 2>&1; then
  echo "== apps/web"
  apps/web/scripts/check.sh
else
  echo "== apps/web skipped: pnpm is not on PATH (CI runs it)"
fi
