# Gogol Pin

The script is the CLI to manipulate the Gogol House database.

## Run the script

Create a copy of `.env.example` file and populate it with the correct database URI:

```shell
cp .env.example .env

echo DATABASE_URI=mysql+aiomysql://user:password@host:port/database > .env
```

Show help message:

```shell
poetry run python -m gogol_pin --help
```

Pin event:

```shell
poetry run python -m gogol_pin pin <event-url> --dry-run
```

Copy event to the new date:

```shell
poetry run python -m gogol_pin copy <event-url> <new-event-date> <new-event-time> --new-price <new-price> --dry-run
```

Export monthly statistics:

```shell
poetry run python -m gogol_pin export <month-number> <year-suffix>
```

Copy chronograph entries:

```shell
poetry run python -m gogol_pin chrono <month-number> <year-suffix> --dry-run
```

## Development

To lint the project run the following command to execute the linting script:

```shell
poetry run ./lint.sh
```

## To-do

- [x] Pin event in agenda
- [x] Why do we need three commits?
- [x] Copy event on a new date
  - [x] Fix issue with date being incorrect
  - [ ] Allow optional time
  - [x] Copy tags
- [x] Export month statistics
- [ ] Send it via email?
- [x] Guide the code
- [x] Update the chronograph (disable old, enable new)
- [ ] Rename to Gogol CLI

## Problems

- Images are not duplicated, but linked more than once to event (originally) and
  to the pin. This can be problematic if we ever delete a pin. It will lead to
  the "deletion" of corresponding image in the event too. 
