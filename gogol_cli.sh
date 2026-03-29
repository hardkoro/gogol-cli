# Gogol CLI shell helper
# Add to ~/.zshrc:
#   export GOGOL_CLI_DIR="/path/to/gogol-cli"
#   source "$GOGOL_CLI_DIR/gogol_cli.sh"

gogol() {
    uv run --project "$GOGOL_CLI_DIR" --env-file "$GOGOL_CLI_DIR/.env" python -m gogol_cli "$@"
}

