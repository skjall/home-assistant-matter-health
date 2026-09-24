#!/usr/bin/env bash
# Lint, types and the full test suite, inside the Python the add-on ships.
#
# The source is mounted read-only and the container runs as the calling user
# without network, so a run cannot change the working tree or reach a real
# Home Assistant. Everything it writes lands in .artefakte/.
#
#   scripts/run_tests.sh              everything
#   scripts/run_tests.sh -k pairing   one slice (pytest arguments)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="matter-health-test:$(sha256sum "$root/pyproject.toml" "$root/Dockerfile.test" | sha256sum | cut -c1-12)"

if ! docker image inspect "$image" >/dev/null 2>&1; then
  docker build -q -t "$image" -f "$root/Dockerfile.test" "$root" >/dev/null
fi

mkdir -p "$root/.artefakte"
run() {
  docker run --rm --network none --user "$(id -u):$(id -g)" \
    -v "$root:/src:ro" -v "$root/.artefakte:/out" \
    -e HOME=/out -e COVERAGE_FILE=/out/.coverage \
    -e MYPY_CACHE_DIR=/out/.mypy_cache -e RUFF_CACHE_DIR=/out/.ruff_cache \
    -w /src "$image" "$@"
}

run ruff check .
run ruff format --check .
run mypy
run python -m pytest -p no:cacheprovider "$@"
