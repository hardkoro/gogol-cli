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

Run the script:

```shell
poetry run python -m gogol_pin pin-event <event-url> --dry-run
```

## Development

To lint the project run the following command to execute the linting script:

```shell
poetry run ./lint.sh
```

## To-do

- [x] Pin event in agenda
- [x] Why do we need three commits?
- [ ] Copy event on a new date
- [ ] Export month statistics
- [ ] Send it via email?
- [ ] Guide the code
- [ ] Update the chronograph (disable old, enable new)

## Problems

- Images are not duplicated, but linked more than once to event (originally) and
  to the pin. This can be problematic if we ever delete a pin. It will lead to
  the "deletion" of corresponding image in the event too. 
