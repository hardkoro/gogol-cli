#!/bin/bash

# Run ruff
uv run ruff format .
uv run ruff check .

# Run ty
uv run ty check gogol_cli
