# Gogol CLI shell helper
# Add to ~/.zshrc:
#   export GOGOL_CLI_DIR="/path/to/gogol-cli"
#   source "$GOGOL_CLI_DIR/gogol_cli.sh"

alias gogol='noglob _gogol_run'

_gogol_run() {
    local args=()
    for arg in "$@"; do
        args+=("${arg%%\?*}")
    done
    uv run --project "$GOGOL_CLI_DIR" --env-file "$GOGOL_CLI_DIR/.env" python -m gogol_cli "${args[@]}"
}

