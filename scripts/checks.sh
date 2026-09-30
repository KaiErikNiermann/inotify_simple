#!/usr/bin/env bash
# Lint, type-check, complexity and test gate. Used by the pre-push hook and CI.
set -euo pipefail
cd "$(dirname "$0")/.."

poetry run ruff check
poetry run ruff format --check
poetry run xenon --max-absolute B src
poetry run pyright
poetry run pytest -q
