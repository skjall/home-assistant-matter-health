# Contributing

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install pre-commit
.venv/bin/pre-commit install
cd matter_health/frontend && npm ci
```

From then on every commit runs ruff, the translation check, and - when Python
changed - `mypy --strict` and the full test suite.

## Tests

```bash
scripts/run_tests.sh              # lint, types, suite, coverage floor 95 %
scripts/run_tests.sh -k pairing   # one slice
```

They run in Docker on Python 3.14, the version the add-on ships, whatever the
workstation has. The source is mounted read-only, the container has no network
and runs as your own user; everything a run produces lands in `.artefakte/`.

Test fixtures are invented. Never copy data from a real installation into a
test - see "No data from a real installation" in [CLAUDE.md](CLAUDE.md).

## Frontend

```bash
cd matter_health/frontend
npm run build      # into matter_health/app/matter_health/web/static/
npm run watch
```

Visual checks live in `tests/visual/` (Playwright): light and dark, 390 px and
1440 px.

## Adding a source, a rule or an explanation

See [docs/architecture.md](docs/architecture.md). In short: a source turns
something it observes into events, a rule turns events into findings, and a
finding is a chain of translation keys. None of the three needs to know about
the others.

## Language

Everything in the repository is English except the translation files. Every UI
string exists in `matter_health/translations/ui/{de,en,es,fr}.json`.

## Commits and releases

[Conventional Commits](https://www.conventionalcommits.org/). release-please
derives the version and the changelog from them: `fix` is a patch, `feat` a
minor release, `!` or `BREAKING CHANGE` a major one; `chore`, `docs`, `ci`,
`test`, `refactor`, `perf` and `build` release nothing.

Work happens on branches named `<type>/<what-it-does>` and reaches `main`
through a pull request whose title is itself a Conventional Commit.
