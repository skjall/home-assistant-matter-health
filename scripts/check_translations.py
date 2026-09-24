#!/usr/bin/env python3
"""Refuse translations that have drifted apart.

English is the reference. Every other language must have exactly the same
keys, the same ``{placeholders}`` in each text, and a plural form wherever
English has one; a missing key would otherwise show up as English in the
middle of a German sentence, and a lost placeholder as a sentence without its
subject. Keys the code asks for literally must exist in English.

Only the standard library is used, so the hook runs without any setup.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "matter_health"
UI = ADDON / "translations" / "ui"
OPTIONS = ADDON / "translations"
REFERENCE = "en"

PLACEHOLDER = re.compile(r"\{(\w+)\}")
PLURAL = " | "
# A literal translation key in Python or TypeScript. Keys built at run time
# (with a placeholder or an f-string) are skipped; their parts are covered by
# the key parity check.
USED_KEY = re.compile(
    r"""["'`]((?:app|nav|status|summary|role|confidence|finding|link|origin|"""
    r"""cause|fix|phase|timeline|network|source|thread_role|generic|time)"""
    r"""\.[a-z0-9_.]+)["'`]"""
)
# Event kinds share the dotted look of keys ("source.status") but are not.
KINDS = ADDON / "app" / "matter_health" / "kinds.py"
CODE = [
    (ADDON / "app", "*.py"),
    (ADDON / "frontend" / "src", "*.ts"),
]


def flatten(tree: dict[str, object], prefix: str = "") -> dict[str, str]:
    """Map every dotted key to its text."""
    flat: dict[str, str] = {}
    for name, value in tree.items():
        key = f"{prefix}{name}"
        if isinstance(value, dict):
            flat.update(flatten(value, f"{key}."))
        else:
            flat[key] = str(value)
    return flat


def option_keys(path: Path) -> set[str]:
    """Return the dotted keys of an options translation file.

    The files are two or three levels of plain ``key:`` mappings, so reading
    the indentation is enough and needs no YAML library.
    """
    keys: set[str] = set()
    stack: list[tuple[int, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        name = line.strip().split(":", 1)[0]
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, name))
        keys.add(".".join(part for _, part in stack))
    return keys


def check_ui(errors: list[str]) -> dict[str, str]:
    """Compare every UI language with English."""
    reference = flatten(json.loads((UI / f"{REFERENCE}.json").read_text("utf-8")))
    for path in sorted(UI.glob("*.json")):
        if path.stem == REFERENCE:
            continue
        other = flatten(json.loads(path.read_text("utf-8")))
        for key in sorted(reference.keys() - other.keys()):
            errors.append(f"{path.name}: missing {key}")
        for key in sorted(other.keys() - reference.keys()):
            errors.append(f"{path.name}: unknown {key}")
        for key in sorted(reference.keys() & other.keys()):
            wanted = set(PLACEHOLDER.findall(reference[key]))
            found = set(PLACEHOLDER.findall(other[key]))
            if wanted != found:
                errors.append(
                    f"{path.name}: {key} has {sorted(found)}, English {sorted(wanted)}"
                )
            if (PLURAL in reference[key]) != (PLURAL in other[key]):
                errors.append(f"{path.name}: {key} plural form differs from English")
    return reference


def check_options(errors: list[str]) -> None:
    """Compare the add-on option translations with English."""
    reference = option_keys(OPTIONS / f"{REFERENCE}.yaml")
    for path in sorted(OPTIONS.glob("*.yaml")):
        if path.stem == REFERENCE:
            continue
        keys = option_keys(path)
        errors.extend(f"{path.name}: missing {k}" for k in sorted(reference - keys))
        errors.extend(f"{path.name}: unknown {k}" for k in sorted(keys - reference))


def check_used(errors: list[str], reference: dict[str, str]) -> None:
    """Every literal key in the code must be translated."""
    kinds = set(re.findall(r'= "([a-z_.]+)"', KINDS.read_text("utf-8")))
    for folder, pattern in CODE:
        for path in sorted(folder.rglob(pattern)):
            if "node_modules" in path.parts or "static" in path.parts:
                continue
            for key in USED_KEY.findall(path.read_text("utf-8")):
                if key.endswith(".") or key in reference or key in kinds:
                    continue
                # A key may name a subtree that the code completes itself.
                if any(known.startswith(f"{key}.") for known in reference):
                    continue
                errors.append(f"{path.relative_to(ROOT)}: no translation for {key}")


def main() -> int:
    """Run all checks and report every problem at once."""
    errors: list[str] = []
    reference = check_ui(errors)
    check_options(errors)
    check_used(errors, reference)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
