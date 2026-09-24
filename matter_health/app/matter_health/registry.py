"""A named collection of plug-ins that register themselves by decorator.

Sources, log parsers and rules each live in a module of their own and add
themselves to a registry when imported. Adding one is creating a file; nothing
central has to list it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator


class Registry[T]:
    """Plug-ins of one kind, looked up by name."""

    def __init__(self, kind: str) -> None:
        """Create an empty registry; ``kind`` appears in error messages."""
        self._kind = kind
        self._entries: dict[str, T] = {}

    def register(self, name: str) -> Callable[[T], T]:
        """Class decorator adding the decorated plug-in under ``name``."""

        def add(entry: T) -> T:
            if name in self._entries:
                raise ValueError(f"{self._kind} {name!r} is registered twice")
            self._entries[name] = entry
            return entry

        return add

    def get(self, name: str) -> T:
        """Return the plug-in registered under ``name``."""
        try:
            return self._entries[name]
        except KeyError:
            raise KeyError(f"no {self._kind} named {name!r}") from None

    def names(self) -> list[str]:
        """All registered names, sorted."""
        return sorted(self._entries)

    def __iter__(self) -> Iterator[T]:
        """Iterate over the plug-ins in name order."""
        return iter(self._entries[name] for name in self.names())

    def __len__(self) -> int:
        """Return the number of registered plug-ins."""
        return len(self._entries)
