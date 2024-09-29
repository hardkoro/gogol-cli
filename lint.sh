#!/bin/bash

# Run ruff
poetry run ruff format .
poetry run ruff check .

# Run pylint
poetry run pylint gogol_pin

# Run mypy
poetry run mypy gogol_pin
