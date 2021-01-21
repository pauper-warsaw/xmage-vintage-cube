#!/usr/bin/env python3.9
"""Generate an up-to-date *Magic Online Vintage Cube* as an XMage deck file.

Given a file and (optionally) a URL, scraps an up-to-date version of
*Magic Online Vintage Cube*, fixing all predictable typos in the process.
Afterwards, for each card in the cube, fetches the oldest non-promotional¹
print of that card if that's possible (excluding certain sets), groups the
cards based on their bucket (identity) in the cube, and exports it in its
entirety (metadata included) as an XMage deck file.

¹ If a card is a released, functionally unique promotional card (e.g. Mana
Crypt, The Walking Dead (**sigh**) cards), then it is procured on the spot.

By default, it will attempt to scrap cube data from Magic's `Mothership
<https://magic.wizards.com/en/articles/archive/vintage-cube-cardlist>`_.

Currently the following sets are excluded (reprint-only or fully reprintable):
   * Limited Edition Alpha
   * Fourth Edition

Sample usage from an interpreter::

    >>> from generate import generate
    >>> generate("test.dck")

This will attempt to download cube data from Mothership and export it to a file
named ``test.dck``.

Best ran with `pyflow <https://github.com/David-OConnor/pyflow#installation>`_
as a quick-and-dirty script, using Python 3.9::

    $ echo 3.9 | pyflow script generate.py "cube.dck"

"""

from __future__ import annotations

from abc import ABC, abstractmethod
import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import cmp_to_key, partial, wraps
import logging
from os import PathLike
from pathlib import Path
import re
import sys
from typing import (Any, Callable, ClassVar, Final, Iterator, NoReturn,
                    Optional, Text, Tuple, Type, Union, cast, final)

from bs4 import BeautifulSoup, Tag
import dateutil.parser
from mtgsdk import Card, Set
import requests

__author__ = "mataha"
__version__ = "0.0.1"
__license__ = "Public domain"

__requires__ = ["beautifulsoup4", "mtgsdk", "python-dateutil", "requests"]

__url__ = "https://magic.wizards.com/en/articles/archive/vintage-cube-cardlist"

StrPath = Union[str, PathLike[str]]
Url = Union[Text, bytes]


def _assemble_logger(name: str) -> logging.Logger:
    format_ = logging.Formatter("[%(levelname)-8s] [%(asctime)s] %(message)s")

    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(format_)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    return logger


log: Final = _assemble_logger(__name__)


class Exporter(ABC):
    """Base exporter for various *Magic* deck editors.

    Even though the sole purpose of this module (right now) is to export
    *Magic Online Vintage Cube* as an XMage deck, this may change soon-ish."""

    def export(self, cube: Cube, file: StrPath) -> None:
        log.info(f"Formatting cube data (format: {self.style})")

        data = self.format(cube)

        log.info("Cube data formatted; ready to export")

        with open(file, 'w', buffering=True, encoding="utf-8") as stream:
            stream.write(data)

        log.info(f"Cube data exported to {file!s}")

    @property
    @abstractmethod
    def style(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def format(self, cube: Cube) -> str:
        raise NotImplementedError


class XMageExporter(Exporter):
    """Exporter for XMage's deck editor."""

    class _UnicodeToAsciiCollectorNumberTransformer:
        """Transformer for non-ASCII collector numbers.

        XMage doesn't tolerate non-ASCII characters in card numbers,
        thus these have to be converted to ASCII one way or another.
        Unfortunately, they do this by hard-coding direct download links..."""

        # https://github.com/magefree/mage/blob/xmage_1.4.47V1/Mage.Client/src/main/java/org/mage/plugins/card/dl/sources/ScryfallImageSupportCards.java
        _TRANSFORMATIONS: ClassVar = {
            # Mainly Arabian Nights, but also Portal Starter Deck, The Dark...
            '†': '+',

            # Planeshift, Deckmasters, War of the Spark (JP Planeswalkers) etc.
            '★': '*',
        }

        def __init__(self) -> None:
            self._transformations = self._TRANSFORMATIONS

        def transform(self, number: str) -> str:
            transformed = number

            for symbol, replacement in self._transformations.items():
                transformed = transformed.replace(symbol, replacement)

            return transformed

        def __call__(self, *args: Any, **kwargs: Any) -> str:
            return self.transform(*args, **kwargs)

    _DIRECTIVES: ClassVar = {
        "Name": "NAME",
        "Author": "AUTHOR"
    }

    STYLE: ClassVar = "XMage"

    def __init__(self) -> None:
        super().__init__()

        self._transformer = self._UnicodeToAsciiCollectorNumberTransformer()

    @property
    def style(self) -> str:
        return self.STYLE

    @staticmethod
    def _bucketize(cube: Cube) -> dict[str, list[CubeEntry]]:
        buckets = defaultdict(list)

        for card in cube:
            buckets[card.bucket].append(card)

        for bucket in buckets.values():
            bucket.sort()  # in-place is fine here

        return buckets

    def format(self, cube: Cube) -> str:
        log.debug("Formatting preamble...")

        result = (f"{self._DIRECTIVES['Name']}:{cube.name} "
                  f"({cube.date:%d.%m.%Y})\n"
                  f"{self._DIRECTIVES['Author']}:{cube.author}\n")

        log.debug("Preamble formatted")

        buckets = self._bucketize(cube)

        log.debug(f"{len(buckets)} buckets total")

        for bucket, cards in buckets.items():
            result += f"\n# {bucket}\n"

            for card in cards:
                code = f"[{card.set_code}:{self._transformer(card.number)}]"
                result += f"{card.quantity} {code} {card.name}\n"

        return result


@final
class CardNameSanitizer:

    #: Common typos in cards present in *Magic Online Vintage Cube*,
    #: as a precaution against random mistakes.
    #: Data gathered from Mothership, CFB and SCG.
    #:
    #: Yes, this could be probably delegated to an external API, but /effort.
    _TYPOS: ClassVar = {
        "Azorious Signet":             "Azorius Signet",
        "Elspeth, Knight Errant":      "Elspeth, Knight-Errant",
        "Hazoret, the Fervent":        "Hazoret the Fervent",
        "Jace, Vryns Prodigy":         "Jace, Vryn's Prodigy",
        "Leonin Relic-Warden":         "Leonin Relic-Warder",
        "Nahiri the Harbinger":        "Nahiri, the Harbinger",
        "Sakura Tribe Elder":          "Sakura-Tribe Elder",
        "Smugglers Copter":            "Smuggler's Copter",
        "Ulamog the Ceaseless Hunger": "Ulamog, the Ceaseless Hunger",
    }

    def __init__(self) -> None:
        self._typos = self._TYPOS

    def __getitem__(self, name: str) -> str:
        return self._typos[name]

    def __contains__(self, name: str) -> bool:
        return name in self._typos

    def sanitize(self, name: str) -> str:
        if name in self:
            log.warning(f"Detected a typo in name '{name}'; fixing...")
            sanitized = self[name]
            log.warning(f"Typo fixed: '{name}' => '{sanitized}'")

            return sanitized

        return name


@final
class SetRepository:

    #: Regular set types. Newly-printed (not reprinted) cards from these sets
    #: are functionally unique.
    #:
    #: Primary product types:
    #:    * ``starter``
    #:    * ``core``
    #:    * ``expansion``
    #:
    #: Supplementary product types:
    #:    * ``commander``
    #:    * ``draft_innovation``
    #:    * ``planechase``
    #:    * ``archenemy``
    #:
    #: Extensions of Modern format (e.g. MH1 + its descendants) are considered
    #: ``draft_innovation``.
    _SET_TYPES: ClassVar = {
        "starter", "core", "expansion",
        "commander", "draft_innovation", "planechase", "archenemy"
    }

    #: Sets that shouldn't appear in a cube (for reasons disclosed below).
    #: Note that these sets should be reprint-only or fully reprintable.
    _BLACKLISTED_SET_CODES: ClassVar = [
        # Limited Edition Alpha - **very** rounded corners and Volcanic Island
        "LEA",

        # Fourth Edition - alternate print runs and other shenanigans
        "4ED"
    ]

    def __init__(self) -> None:
        log.info(f"Fetching set data (ignoring {self._BLACKLISTED_SET_CODES})")
        log.debug(f"For set types: {self._SET_TYPES}")

        sets = {s.code: s
                for s in Set.where(type=','.join(self._SET_TYPES)).all()
                if s.code not in self._BLACKLISTED_SET_CODES}

        log.info(f"Set data obtained: {len(sets)} sets total")

        self._sets = sets

    def __getitem__(self, code: str) -> Set:
        return self._sets[code]

    def __contains__(self, code: str) -> bool:
        return code in self._sets


@final
class ExtraCardRepository:

    #: Cards that, even though not normally found in regular Magic sets, can
    #: appear in Vintage Cube (i.e. are released, functionally unique
    #: promotional cards).
    _EXTRAS: ClassVar = {
        # Dragon*Con (also Japanese Redemption Program)
        # https://magic.wizards.com/en/articles/archive/nalathni-dragon-2003-04-28
        "Nalathni Dragon":             ("PDRC", "1"),

        # HarperPrism Book Promos
        # Note that the numbering is based on their release date.
        # https://mtg.fandom.com/wiki/HarperPrism
        # https://www.magiclibrarities.net/109-rarities-harperprism-book-inserts-english-cards-index.html
        "Arena":                       ("PHPR", "1"),
        "Sewers of Estark":            ("PHPR", "2"),
        "Windseeker Centaur":          ("PHPR", "3"),
        "Giant Badger":                ("PHPR", "4"),
        "Mana Crypt":                  ("PHPR", "5"),

        # Secret Lair Drop Series: The Walking Dead
        # https://magic.wizards.com/en/articles/archive/news/walking-dead-shambles-secret-lair-2020-09-28
        "Rick, Steadfast Leader":      ("SLD",  "143"),
        "Daryl, Hunter of Walkers":    ("SLD",  "144"),
        "Glenn, the Voice of Calm":    ("SLD",  "145"),
        "Michonne, Ruthless Survivor": ("SLD",  "146"),
        "Negan, the Cold-Blooded":     ("SLD",  "147"),
        "Lucille":                     ("SLD",  "581"),

        # Black-bordered cards that, even though not legal in any
        # format, can (for one reason or another) appear in a cube
        # (rather due to a bug than being included out of the blue).

        # MicroProse Promos (based on Astral Cards from Shandalar)
        # https://magic.wizards.com/en/articles/archive/feature/astral-cards-2009-02-12
        "Aswan Jaguar":                ("PMIC", "1"),

        # Cards that are only available on Magic Online, but can
        # (for one reason or another) appear in a cube (rather
        # due to a bug than being included out-of-the-blue). See:
        # https://old.reddit.com/r/magicTCG/comments/5j2wl7/fyi_gleemox_is_playable_on_mtgo_right_now/

        # https://magic.wizards.com/en/articles/archive/arcana/ask-wizards-gleemox-and-elves-deep-shadow-2008-12-09
        "Gleemox":                     ("PRM",  "26584")

        # I genuinely hope I won't have to extend this...
    }

    def __init__(self) -> None:
        log.info("Setting up data about extra cards...")

        self._cards = self._EXTRAS

        log.info(f"Extra card data set: {len(self._EXTRAS)} cards total")

    def __getitem__(self, name: str) -> Tuple[str, str, str]:
        return (name,) + self._cards[name]

    def __contains__(self, name: str) -> bool:
        return name in self._cards


_chunkify_regex = re.compile(r"\d+|\D+")


def _chunkify(string: str) -> list[Any]:
    return [int(chunk) if chunk.isnumeric() else chunk
            for chunk in _chunkify_regex.findall(string)]


def _card_date(card: Card, set_source: SetRepository) -> str:
    return set_source[card.set].release_date


def _card_compare(this: Card, other: Card, set_source: SetRepository) -> int:
    this_date = _card_date(this, set_source)
    other_date = _card_date(other, set_source)

    if this_date != other_date:
        return 1 if this_date > other_date else -1

    this_chunks = _chunkify(this.number)
    other_chunks = _chunkify(other.number)

    if this_chunks != other_chunks:
        return 1 if this_chunks > other_chunks else -1

    return 0


CardFetcher = Callable[['CubeEntryMapper', str], Card]
CardTranslator = Callable[['CubeEntryMapper', str], Tuple[str, str, str]]


def translator(fetcher: CardFetcher) -> CardTranslator:
    separator = " // "

    def serialize_card_by_name(name: str) -> str:
        log.debug(f"Serialization check (name: '{name}')")

        if separator in name:
            # Every sub-card has a unique Multiverse name,
            # and thus only one split is required.
            log.info(f"Serializing card (name: '{name}')")
            serialized, _ = name.split(separator, maxsplit=1)
            log.info(f"Card serialized as '{serialized}'")

            return serialized

        return name

    def deserialize_card(card: Card, name: str) -> str:
        names = card.names

        log.debug(f"Deserialization check (names: '{names}')")

        # https://docs.magicthegathering.io/#api_v1cards_list name, layout
        if names and name not in names:
            # If the card's full name is not a sub-name itself, merge them all.
            log.info(f"Deserializing card (names: {names})")
            deserialized = separator.join(names)
            log.info(f"Card deserialized as '{deserialized}'")

            return deserialized

        return card.name

    @wraps(fetcher)
    def translate(self, *args: Any, **kwargs: Any) -> Tuple[str, str, str]:
        card = fetcher(self, serialize_card_by_name(*args, **kwargs))

        return deserialize_card(card, *args, **kwargs), card.set, card.number

    return translate


@final
class CubeEntryMapper:

    def __init__(self, extra_card_repo: ExtraCardRepository,
                 set_repo: SetRepository) -> None:
        self._extra_card_repo = extra_card_repo
        self._set_repo = set_repo

    @translator
    def _fetch_oldest(self, name: str) -> Card:
        log.debug(f"Querying remote API for cards with '{name}'")

        cards = [card
                 for card in Card.where(name=name).all()
                 if card.name == name
                 if card.set in self._set_repo]

        log.debug(f"Returned {len(cards)} hits for cards with '{name}'")

        comparator = partial(_card_compare, set_source=self._set_repo)

        return min(cards, key=cmp_to_key(comparator))

    def _pull_from_api(self, entry: RawCubeEntry) -> CubeEntry:
        log.info(f"Fetching card '{entry.name}' (source: MTG API)")

        name, code, number = self._fetch_oldest(entry.name)

        log.info(f"Obtained card '{name}' "
                 f"(set code: {code}, collector number: {number})")

        return CubeEntry(name, number, code, entry.bucket)

    def _pull_from_extra(self, entry: RawCubeEntry) -> CubeEntry:
        log.info(f"Fetching card '{entry.name}' (source: this module)")

        name, code, number = self._extra_card_repo[entry.name]

        log.info(f"Obtained card '{name}' "
                 f"(set code: {code}, collector number: {number})")

        return CubeEntry(name, number, code, entry.bucket)

    def map(self, entry: RawCubeEntry) -> CubeEntry:
        name = entry.name

        log.debug(f"Mapping card '{name}'")

        if name in self._extra_card_repo:
            return self._pull_from_extra(entry)
        else:
            return self._pull_from_api(entry)


@final
class CubeScraper:

    def __init__(self, sanitizer: CardNameSanitizer,
                 *, parser: str = "html.parser") -> None:
        self._sanitizer = sanitizer
        self._parser = parser

    @staticmethod
    def _get_name(content: Tag) -> str:
        suffix = "Cardlist"

        tag = content.h1

        value = tag.text.removesuffix(suffix).rstrip()
        log.debug(f"Name: {value}")

        return value

    @staticmethod
    def _get_date(content: Tag) -> datetime:
        prefix = "Updated:"
        selector = "div #content-detail-page-of-an-article p:nth-child(2)"

        tag = content.select_one(selector)

        value = tag.text.removeprefix(prefix).lstrip()
        log.debug(f"Date: {value}")

        return dateutil.parser.parse(value)

    @staticmethod
    def _get_author(content: Tag) -> str:
        prefix = "By"
        selector = "div .author p:nth-of-type(1)"

        tag = content.select_one(selector)

        value = tag.text.removeprefix(prefix).lstrip()
        log.debug(f"Author: {value}")

        return value

    @staticmethod
    def _get_entries(content: Tag,
                     sanitizer: CardNameSanitizer) -> list[RawCubeEntry]:
        css_class = "collapsibleBlock"

        table = content.find_all("div", class_=css_class)[1].table
        rows = table.tbody.find_all("tr")

        elements = []

        for row in rows:
            first, second, *_ = row.find_all("td")

            name = sanitizer.sanitize(first.text)
            bucket = second.text

            elements.append(RawCubeEntry(name, bucket))

        return elements

    @staticmethod
    def _scrap(url: Url) -> str:
        headers = {
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/79.0.3945.130 "
                          "Safari/537.36",
            "Accept": "text/html,"
                      "application/xhtml+xml,"
                      "application/xml;q=0.9,"
                      "image/webp,"
                      "image/apng,"
                      "*/*;q=0.8,"
                      "application/signed-exchange;v=b3;q=0.9",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Referer": "https://duckduckgo.com/",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en-GB;q=0.9,en;q=0.8",
        }

        log.debug(f"Headers: {headers}")

        response = requests.get(url, headers=headers)
        log.debug(f"Response status code: {response.status_code}")
        response.raise_for_status()

        return response.text

    def execute(self, url: Url) -> RawCube:
        log.info(f"Scraping data from {url!s}")

        content = BeautifulSoup(self._scrap(url), features=self._parser)

        log.info("Scraping completed successfully!")

        log.info(f"Processing cube data using {self._parser}")

        name = self._get_name(content)
        date = self._get_date(content)
        author = self._get_author(content)
        entries = self._get_entries(content, self._sanitizer)

        log.info(f"Cube data: {name} ({date:%d.%m.%Y}) by {author}, "
                 f"{len(entries)} entries total")

        return RawCube(name, date, author, entries)


@dataclass(frozen=True, order=True)
class RawCubeEntry:

    name: str
    bucket: str


@dataclass(frozen=True)
class RawCube:

    name: str
    date: datetime
    author: str
    entries: list[RawCubeEntry]

    def __iter__(self) -> Iterator[RawCubeEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


@dataclass(frozen=True, order=True)
class CubeEntry:

    # https://www.python.org/dev/peps/pep-0591/#semantics-and-examples ClassVar
    _SINGLETON: ClassVar[int] = 1

    name: str
    number: str  # can contain non-digits, sadly (e.g. 'mb62sb', '221s★' etc.)
    set_code: str
    bucket: str
    quantity: int = _SINGLETON  # a cube is *technically* strictly singleton


@dataclass(frozen=True)
class Cube:

    name: str
    date: datetime
    author: str
    entries: list[CubeEntry]

    def __iter__(self) -> Iterator[CubeEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def from_raw(cls, raw: RawCube, mapper: CubeEntryMapper) -> Cube:
        return cls(raw.name, raw.date, raw.author, [mapper.map(entry)
                                                    for entry in raw])


def _export(raw: RawCube, file: StrPath, /, exporter: Type[Exporter]) -> None:
    mapper = CubeEntryMapper(ExtraCardRepository(), SetRepository())

    exporter().export(Cube.from_raw(raw, mapper), file)


def generate(file: StrPath, url: Optional[Url] = None) -> None:
    """Generate an up-to-date *Magic Online Vintage Cube*
    as an XMage deck file."""

    if url is None:
        url = __url__

    with open(file, 'a+', encoding="utf-8", errors="ignore") as stream:
        assert not stream.closed

    scrap = CubeScraper(CardNameSanitizer()).execute(url)

    _export(scrap, file, XMageExporter)


def _abort(message: str) -> NoReturn:
    import traceback

    program = Path(sys.argv[0]).name
    reason = traceback.extract_stack(None, 2)[0][2].lstrip("_")

    print(f"{program}: {reason}: {message}", file=sys.stderr)
    sys.exit(True)


def _error(exception: Exception) -> NoReturn:
    message = exception.__str__()
    error: str

    if isinstance(message, bytes) and message:
        error = cast(bytes, message).decode()
    elif isinstance(message, str) and message:
        error = message
    else:
        error = type(exception).__name__

    _abort(error)


def _interrupt() -> NoReturn:
    _abort("interrupted by the user")


def _main(argv: Optional[list[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(epilog="example: %(prog)s deck.dck",
                                     fromfile_prefix_chars='@')

    parser.add_argument("--url", "-u", help="URL to scrap cube data from")
    parser.add_argument("file", help="file to write cube contents to")
    parser.add_argument("--version", action="version", version=__version__)

    args = parser.parse_args(argv)

    try:
        generate(args.file, args.url)

    except Exception as exception:
        _error(exception)

    except KeyboardInterrupt:
        _interrupt()


if __name__ == "__main__":
    _main()
