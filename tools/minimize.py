#!/usr/bin/env python3.9
"""Minimize an XMage deck file.

Minimizes an XMage deck file, stripping every comment and empty line.

A valid XMage deck file has a ``.dck`` extension (and that's really everything
Java's ``JFileChooser`` cares about).

Example usage::

    $ python tools/minimize.py "deck.dck"

This will minimize a file ``deck.dck`` into ``deck.min.dck`` (name by default).

Run it with Python 3.9::

    $ python3.9 tools/minimize.py "cube.dck"

Or with `pyflow <https://github.com/David-OConnor/pyflow#installation>`_ as a
quick-and-dirty script::

    $ echo 3.9 | pyflow script tools/minimize.py "cube.dck"

"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import ClassVar, NoReturn, Optional, Union, cast, final
import unittest

# https://github.com/python/typeshed/blob/c5ed22a24e8f14a7d78c0d9824f7307229c3e561/stdlib/2and3/_typeshed/__init__.pyi#L59
StrPath = Union[str, os.PathLike[str]]

__all__ = ["minimize"]

__version__ = "0.1.0"
__author__ = "mataha & pauper-warsaw"


@final
class XMageDeckFile:
    """Represents an XMage deck file."""

    def __init__(self, file: StrPath) -> None:
        self._file = Path(file)

        if not self._is_valid_xmage_file(self._file):
            raise ValueError(f"file is not a valid XMage deck file: '{file}'")

    def __str__(self) -> str:
        return str(self._file)

    def minimize(self, to: Optional[StrPath] = None) -> None:
        """Minimizes this deck file, stripping every comment and empty line."""
        if to is None:
            to = self._minimized_name()

        with open(self._file, "r") as in_stream, open(to, "w") as out_stream:
            for line in in_stream:
                if not _is_xmage_ignored(line):
                    out_stream.write(line)

    _MINIMIZED_EXTENSION: ClassVar = ".min"

    def _minimized_name(self) -> StrPath:
        return self._file.stem + self._MINIMIZED_EXTENSION + self._file.suffix

    _XMAGE_FILE_EXTENSION: ClassVar = ".dck"

    @classmethod
    def _is_valid_xmage_file(cls, path: Path) -> bool:
        return path.suffix == cls._XMAGE_FILE_EXTENSION


# https://github.com/magefree/mage/blob/xmage_1.4.47V1/Mage/src/main/java/mage/cards/decks/importer/DckDeckImporter.java#L38-L40
def _is_xmage_ignored(line: str) -> bool:
    return _is_empty(line) or _is_comment(line)


def _is_empty(line: str) -> bool:
    return not line.strip()


def _is_comment(line: str, *, comment_prefix: str = "#") -> bool:
    return line.startswith(comment_prefix)


def minimize(file: StrPath, to: Optional[StrPath] = None) -> None:
    """Minimizes an XMage deck file, stripping every comment and empty line."""
    XMageDeckFile(file).minimize(to)


def _abort(message: str) -> NoReturn:
    import traceback

    program = Path(sys.argv[0]).name
    cause = traceback.extract_stack(None, 2)[0][2].lstrip("_")

    sys.exit(f"{program}: {cause}: {message}")


def _error(exception: Exception) -> NoReturn:
    message = type(exception).__name__

    error = exception.__str__()

    if error:
        reason = ": "

        if isinstance(error, bytes):
            reason += cast(bytes, error).decode()
        elif isinstance(error, str):
            reason += error

        message += reason

    _abort(message)


def _main(argv: Optional[list[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description="minimize an XMage deck file",
        epilog="example: %(prog)s deck.dck",
        fromfile_prefix_chars='@'
    )

    parser.add_argument("deck", help="an XMage deck file to minimize")
    parser.add_argument("--version", action="version", version=__version__)

    args = parser.parse_args(argv)

    try:
        minimize(args.deck)

    except Exception as exception:
        _error(exception)


if __name__ == "__main__":
    _main()


# Run these with ``python -m unittest "tools/minimize.py"``
class MinimizeTestCase(unittest.TestCase):

    @staticmethod
    def _count_lines(file: StrPath) -> int:
        lines: int

        with open(file, "r") as stream:
            lines = sum(1 for _ in stream)

        return lines

    @staticmethod
    def _resource(resource: str) -> StrPath:
        this = Path(__file__).resolve()  # rewind all symlinks first
        path = this.parent / resource

        return path.resolve()

    def test_minimize_vintage_cube(self) -> None:
        import tempfile

        deck = self._resource("../cube.dck")

        expected = 540 + 2  # 540 lines of cards + 2 lines of metadata

        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "test.dck"
            minimize(deck, file)

            actual = self._count_lines(file)

            self.assertEqual(expected, actual)

    def test_minimize_this_script(self) -> None:
        import tempfile

        deck = self._resource(__file__)

        expected = ValueError

        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "test.dck"

            with self.assertRaises(expected):
                minimize(deck, file)
