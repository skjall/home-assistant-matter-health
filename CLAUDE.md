# Working in this repository

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/architecture.md](docs/architecture.md)
first. The rules below are the ones that have gone wrong before.

## Language

**The conversation may be German. Everything in the repository is English.**
Code, comments, docstrings, log messages, error strings, documentation, commit
messages, PR titles and bodies. No exceptions.

The single exception is the translation files. User-facing words are never
hard-coded: they live in `matter_health/translations/ui/{de,en,es,fr}.json`
(the add-on UI) and `matter_health/translations/{de,en,es,fr}.yaml` (the add-on
options). Every key exists in all four languages; `scripts/check_translations.py`
refuses a commit otherwise.

## No data from a real installation

Nothing from anyone's real Home Assistant goes into the repository: no device
or person names, room names, IP addresses, host names, Thread extended
addresses, PAN IDs, node IDs, entity IDs or timestamps of a real incident. Not
in code, comments, docs, tests, fixtures or commit messages.

Examples and fixtures are invented: `Living Room Plug`, `fd00:db8::/64`,
`192.0.2.10`, `0x1234`. When a finding from a live analysis turns into code,
abstract the pattern and replace every concrete value.

## Comments explain why, not what happened

A comment says why the code does what it does, in terms of how Matter, Thread
or Home Assistant behave: "Thread elects a new leader only after the leader
timeout, so a partition can last about a minute." It never tells the story of
the incident that led to it.

## Words for users

A finding is read by someone who has never heard of PASE, CASE, RLOC16 or a
network partition. The UI says what happened, what it means for them and what
to do, in everyday words. Technical detail is available behind "Details", never
in the headline. "Failed at OperationalCredentials.Certificates" is not an
explanation; "Home Assistant could not hand the device its key to your home"
is, followed by what to try.

## Before you commit

- `scripts/run_tests.sh` - ruff, `mypy --strict` and the suite with a 95 %
  coverage floor, in Docker on Python 3.14. The pre-commit hook runs it.
- A UI change is not done until it has been looked at in a browser: light and
  dark, 390 px and 1440 px. Screenshots, not reasoning.
- Commits follow Conventional Commits; `scripts/check_commit_message.py` is the
  commit-msg hook.

## Deploying for a test

`./deploy.sh` copies the working tree into the running add-on container on the
test host and restarts it. Configure it in `.env` (template: `.env.example`).
It is a test deploy only: a rebuild of the add-on replaces it.

## Never

- Never read the OpenThread Border Router's dataset endpoints
  (`/node/dataset/*`). They return the network key in clear text, and this
  add-on has no use for it.
- Never send a command to the Matter Server that changes anything. The add-on
  observes; it does not commission, remove, interview or update devices.
- Never push or open a PR without the maintainer's explicit go.
