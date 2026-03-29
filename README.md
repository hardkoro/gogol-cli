# Gogol CLI

The script is the CLI to manipulate the Gogol House database.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — install via `curl -LsSf https://astral.sh/uv/install.sh | sh`
- An SSH key pair with access to the remote server — generate one if needed:
  ```shell
  ssh-keygen -t ed25519 -C "your_email@example.com"
  ssh-copy-id -i ~/.ssh/id_rsa.pub user@host
  ```

## Installation

```shell
uv sync
```

## Configuration

Copy `.env.example` and populate it:

```shell
cp .env.example .env
```

| Variable | Description | Required |
|---|---|---|
| `DATABASE_URI` | SQLAlchemy async DB URI | ✅ always |
| `SSH_HOST` | Remote server IP / hostname | ✅ always |
| `SSH_USERNAME` | SSH login username | ✅ always |
| `SSH_KEY_PATH` | Path to private SSH key | ✅ always |
| `SSH_BASE_PATH` | Absolute upload path on the server | ✅ always |
| `SMTP_HOST` | SMTP server hostname | ✅ `export` (non-dry) |
| `SMTP_PORT` | SMTP server port | ✅ `export` (non-dry) |
| `SMTP_USERNAME` | SMTP login | ✅ `export` (non-dry) |
| `SMTP_PASSWORD` | SMTP password | ✅ `export` (non-dry) |
| `FROM_ADDR` | Sender email address | ✅ `export` (non-dry) |
| `TO_ADDR` | Recipient email address | ✅ `export` (non-dry) |

## Usage

Show help:

```shell
uv run --env-file .env python -m gogol_cli --help
```

Pin event:

```shell
uv run --env-file .env python -m gogol_cli pin <event-url> [--dry-run]
```

Copy event to a new date:

```shell
uv run --env-file .env python -m gogol_cli copy <event-url> <new-date> <new-time> [--new-price <price>] [--dry-run]
```

Export monthly statistics:

```shell
uv run --env-file .env python -m gogol_cli export <month-number> <year-suffix> [--dry-run]
```

Copy chronograph entries:

```shell
uv run --env-file .env python -m gogol_cli chrono <month-number> <year-suffix> [--dry-run]
```

## Shell alias

Add the following to `~/.zshrc` to use `gogol` as a short alias from anywhere:

```zsh
export GOGOL_CLI_DIR="/path/to/gogol-cli"
source "$GOGOL_CLI_DIR/gogol_cli.sh"
```

Then reload your shell:

```shell
source ~/.zshrc
```

After that, all commands shorten to:

```shell
gogol pin <event-url>
gogol copy <event-url> <new-date> <new-time>
gogol export <month-number> <year-suffix>
gogol chrono <month-number> <year-suffix>
```

## Development

```shell
uv run ./lint.sh
```
