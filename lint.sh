#!/bin/bash

# Format markdown
uv run mdformat README.md

# Run ruff
uv run ruff format .
uv run ruff check .

# Run ty
uv run ty check gogol_cli
