# Gogol CLI

The script is the CLI to manipulate the Gogol House database.

## Installation

Install project dependencies using Poetry:

```shell
poetry install
```

## Run the script

Create a copy of `.env.example` file and populate it with the correct database URI:

```shell
cp .env.example .env

echo DATABASE_URI=mysql+aiomysql://user:password@host:port/database > .env
```

Additionally, if you want to send emails with exported monthly statistics, export
SMTP and email config as follows:

```shell
echo SMTP_HOST=smtp_host > .env
echo SMTP_PORT=smtp_port > .env
echo SMTP_USERNAME=smtp_username > .env
echo SMTP_PASSWORD=smtp_password > .env

echo FROM_ADDR=from_addr > .env
echo TO_ADDRS=to_addrs > .env
```

Show help message:

```shell
poetry run python -m gogol_cli --help
```

Pin event:

```shell
poetry run python -m gogol_cli pin <event-url> --dry-run
```

Copy event to the new date:

```shell
poetry run python -m gogol_cli copy <event-url> <new-event-date-str> --new-event-time-str <new-event-time-str> --new-price <new-price> --dry-run
```

Export monthly statistics:

```shell
poetry run python -m gogol_cli export <month-number> <year-suffix> --dry-run
```

Copy chronograph entries:

```shell
poetry run python -m gogol_cli chrono <month-number> <year-suffix> --dry-run
```

## Development

To lint the project run the following command to execute the linting script:

```shell
poetry run ./lint.sh
```

## Known Issues

- Images are not duplicated, but linked more than once to event (originally) and
  to the pin. This can be problematic if we ever delete a pin. It will lead to
  the "deletion" of corresponding image in the event too.