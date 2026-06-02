# Gogol CLI

CLI for managing the Gogol House database.

## Setup

```shell
uv sync
cp .env.example .env  # fill in DATABASE_URI, SSH_*, and SMTP_* (export only)
```

Add the shell alias to `~/.zshrc` for a shorter `gogol` command:

```zsh
export GOGOL_CLI_DIR="/path/to/gogol-cli"
source "$GOGOL_CLI_DIR/gogol_cli.sh"
```

## Commands

| Command                          | Description                                                           |
| -------------------------------- | --------------------------------------------------------------------- |
| `gogol pin <url>…`               | Pin one or more events                                                |
| `gogol copy <url> <date> <time>` | Copy an event to a new date/time                                      |
| `gogol xcopy`                    | Copy events using Russian natural-language dates (interactive, stdin) |
| `gogol add <folder>`             | Add new events from `.docx` files                                     |
| `gogol exhibit <folder>`         | Create an exhibition from numbered `.docx` files                      |
| `gogol virtual <folder>`         | Create a virtual exhibition from a `.doc`/`.docx` + images            |
| `gogol books <folder>`           | Add books to **Новые поступления** (or **Гоголиана** with `--gogol`)  |
| `gogol export <month> <yy>`      | Export monthly statistics by email                                    |
| `gogol chrono <month> <yy>`      | Copy chronograph entries from 5 years ago                             |

All commands accept `--dry-run`. Run `gogol <command> --help` for full options.

## Development

```shell
uv run ./lint.sh
```
