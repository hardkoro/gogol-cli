#!/bin/bash

# Run ruff
uv run ruff format .
uv run ruff check .

# Run pylint
uv run pylint gogol_cli

# Run mypy
uv run mypy gogol_cli
