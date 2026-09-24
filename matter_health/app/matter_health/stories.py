"""Which findings are part of a larger story.

One cause often has several visible consequences: a border router loses
power, the mesh splits, and a pairing attempt in the same minute fails. Each
rule reports what it sees, but the user should read one story, not three
unrelated cards. Rules declare which stories their findings can belong to
(``Rule.part_of``) and how far a story reaches in time
(``Rule.story_margin``); this module matches them up when findings are read,
so it does not matter which rule published first.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from .engine import Rule
from .model import Finding


def stories(
    findings: list[Finding], rules: Iterable[type[Rule]], now: datetime
) -> dict[str, str]:
    """Map the key of each finding that belongs to a story to the story's key."""
    by_name = {rule.name: rule for rule in rules}
    tellers: list[tuple[Finding, timedelta, timedelta]] = []
    for finding in sorted(findings, key=lambda f: f.started_at):
        rule = by_name.get(finding.rule)
        if rule is not None and rule.story_margin is not None:
            tellers.append((finding, *rule.story_margin))
    belongs: dict[str, str] = {}
    for finding in findings:
        rule = by_name.get(finding.rule)
        wanted = rule.stories_for(finding) if rule else frozenset()
        for story, before, after in tellers:
            if story.rule not in wanted or story.key == finding.key:
                continue
            end = story.ended_at or now
            if story.started_at - before <= finding.started_at <= end + after:
                belongs[finding.key] = story.key
                break
    return belongs
