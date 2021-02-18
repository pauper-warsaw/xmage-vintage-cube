#!/usr/bin/env python3.9
"""Generate a *Magic Online* cube as an XMage deck file, sequentially.

Given a file and (optionally) a URL, scraps an up-to-date version of
a *Magic Online* cube from a site in a Wizards of the Coast article format
while fixing all common typos in its listing.

Afterwards, for each card in the cube, fetches the oldest non-promotional¹
print of that card (excluding certain sets if that's possible), groups the
cards based on their bucket (identity) in the cube, and exports it in its
entirety (metadata included) as an XMage deck file.

Currently the following sets are excluded (reprint-only or fully-reprintable):
   * Limited Edition Alpha
   * Fourth Edition

By default, it will attempt to scrap *Magic Online Vintage Cube* data from
`<https://magic.wizards.com/en/articles/archive/vintage-cube-cardlist>`_.

¹ If a card is a released, functionally unique promotional card (e.g. Mana
Crypt, The Walking Dead (**sigh**) cards), then it is procured on the spot.

Example usage::

    $ python generate.py "deck.dck"

This will attempt to download *Magic Online Vintage Cube* data from Mothership
(above URL by default) and export it to a file named ``deck.dck``.

Run it with Python 3.9 (assuming required dependencies are available)::

    $ python3.9 generate.py "cube.dck"

Or with `pyflow <https://github.com/David-OConnor/pyflow#installation>`_ as a
quick-and-dirty script (dependencies are not needed beforehand this way)::

    $ echo 3.9 | pyflow script generate.py "cube.dck"

"""

from __future__ import annotations

import abc
import argparse
import collections
import dataclasses
from datetime import datetime
import functools
import logging
import os
from pathlib import Path
import re
import sys
from typing import (
    Any,
    Callable,
    ClassVar,
    Final,
    Iterator,
    NoReturn,
    Optional,
    Text,
    Tuple,
    Type,
    Union,
    cast,
    final,
)

__requires__ = ["beautifulsoup4", "python-dateutil", "mtgsdk", "requests"]

from bs4 import BeautifulSoup, Tag  # type: ignore[import]
import dateutil.parser
from mtgsdk import Card, Set  # type: ignore[import]
import requests

StrPath = Union[str, os.PathLike[str]]
Url = Union[Text, bytes]

__all__ = ["generate"]

__version__ = "0.1.0"
__author__ = "mataha & pauper-warsaw"
__license__ = "Public domain"


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


class Exporter(metaclass=abc.ABCMeta):
    """Base exporter for various *Magic* deck editors."""

    __slots__ = ()

    def export(self, cube: Cube, file: StrPath) -> None:
        log.info(f"Formatting cube data (format: {self.style})")

        data = self.format(cube)

        log.info("Cube data formatted; ready to export")

        with open(file, "w", buffering=True, encoding="utf-8") as stream:
            stream.write(data)

        log.info(f"Cube data exported to {file!s}")

    @property
    @abc.abstractmethod
    def style(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def format(self, cube: Cube) -> str:
        raise NotImplementedError

    @classmethod
    def __subclasshook__(cls, subclass: Any) -> Any:
        return NotImplemented


class XMageExporter(Exporter):
    """Exporter for XMage's deck editor."""

    class _UnicodeToAsciiCollectorNumberTransformer:
        """Transformer for non-ASCII collector numbers.

        XMage doesn't tolerate non-ASCII characters in card numbers,
        thus these have to be converted to ASCII one way or another.
        Unfortunately, they do this by hard-coding direct download links...
        """

        # https://github.com/magefree/mage/blob/xmage_1.4.47V1/Mage.Client/src/main/java/org/mage/plugins/card/dl/sources/ScryfallImageSupportCards.java
        _TRANSFORMATIONS: ClassVar = {
            # Mainly Arabian Nights, but also Portal Starter Deck, The Dark...
            "†": "+",

            # Planeshift, Deckmasters, War of the Spark (JP Planeswalkers) etc.
            "★": "*",
        }

        @functools.cached_property
        def _transformations(self) -> dict[str, str]:
            return self._TRANSFORMATIONS

        def __iter__(self) -> Iterator[Tuple[str, str]]:
            return iter(self._transformations.items())

        def transform(self, number: str) -> str:
            transformed = number

            for symbol, replacement in self:
                transformed = transformed.replace(symbol, replacement)

            return transformed

        def __call__(self, *args: Any, **kwargs: Any) -> str:
            return self.transform(*args, **kwargs)

    def __init__(self) -> None:
        super().__init__()

        self._transform = self._UnicodeToAsciiCollectorNumberTransformer()

    STYLE: ClassVar = "XMage"

    @property
    def style(self) -> str:
        return self.STYLE

    @staticmethod
    def _bucketize(cube: Cube) -> dict[str, list[Tuple[CubeEntry, int]]]:
        log.debug("Sorting buckets...")

        buckets = collections.defaultdict(list)

        for card, quantity in cube:
            buckets[card.bucket].append((card, quantity))

        for name, bucket in buckets.items():
            bucket.sort()

            log.debug(f"Sorted bucket: {name}")

        log.debug(f"{len(buckets)} buckets sorted")

        return buckets

    _DIRECTIVES: ClassVar = {"Name": "NAME", "Author": "AUTHOR"}

    @classmethod
    def _preamble(cls, cube: Cube) -> str:
        log.debug("Formatting preamble...")

        name = f"{cls._DIRECTIVES['Name']}:{cube.name}"
        date = f"{cube.date:%d.%m.%Y}"
        author = f"{cls._DIRECTIVES['Author']}:{cube.author}"

        preamble = f"{name} ({date})\n{author}\n"

        log.debug("Preamble formatted")

        return preamble

    def format(self, cube: Cube) -> str:
        content = ""

        for bucket, cards in self._bucketize(cube).items():
            content += f"\n# {bucket}\n"

            for card, quantity in cards:
                version = f"[{card.set_code}:{self._transform(card.number)}]"
                content += f"{quantity} {version} {card.name}\n"

        return self._preamble(cube) + content


@final
class CardNameSanitizer:

    #: Common typos in cards present in *Magic Online Vintage Cube*,
    #: as a precaution against random mistakes.
    #: Data gathered from Mothership, CFB and SCG.
    #:
    #: Yes, this could be probably relegated to an external API, but /effort.
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

    @functools.cached_property
    def _typos(self) -> dict[str, str]:
        return self._TYPOS

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
        # Primary product types
        "starter", "core", "expansion",

        # Supplementary product types
        "commander", "draft_innovation", "planechase", "archenemy",
    }

    #: Sets that shouldn't appear in a cube (for reasons disclosed below).
    #: Note that these sets should be reprint-only or fully reprintable.
    _BLACKLISTED_SET_CODES: ClassVar = [
        # Limited Edition Alpha - **very** rounded corners and Volcanic Island
        "LEA",

        # Fourth Edition - alternate print runs and other shenanigans
        "4ED",
    ]

    @functools.cached_property
    def _sets(self) -> dict[str, Set]:
        log.info(f"Fetching set data (ignored: {self._BLACKLISTED_SET_CODES})")
        log.debug(f"For set types: {self._SET_TYPES}")

        sets = {
            s.code: s
            for s in Set.where(type=",".join(self._SET_TYPES)).all()
            if s.code not in self._BLACKLISTED_SET_CODES
        }

        log.info(f"Set data obtained: {len(sets)} sets total")

        return sets

    def __getitem__(self, code: str) -> Set:
        return self._sets[code]

    def __contains__(self, code: str) -> bool:
        return code in self._sets


CardInfo = Tuple[str, str, str]


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

    @functools.cached_property
    def _cards(self) -> dict[str, Tuple[str, str]]:
        log.info("Setting up data about extra cards...")
        extras = self._EXTRAS
        log.info(f"Extra card data set: {len(extras)} cards total")

        return extras

    def __getitem__(self, name: str) -> CardInfo:
        return (name,) + self._cards[name]

    def __contains__(self, name: str) -> bool:
        return name in self._cards


def translator(
    fetcher: Callable[[CubeEntryMapper, str], Card]
) -> Callable[[CubeEntryMapper, str], CardInfo]:
    separator: Final = " // "

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

    @functools.wraps(fetcher)
    def translate(self, *args: Any, **kwargs: Any) -> CardInfo:
        card = fetcher(self, serialize_card_by_name(*args, **kwargs))
        name = deserialize_card(card, *args, **kwargs)

        return name, card.set, card.number

    return translate


_chunkify_regex = re.compile(r"\d+|\D+")


def _chunkify(string: str) -> list[Any]:
    return [
        int(chunk) if chunk.isnumeric() else chunk
        for chunk in _chunkify_regex.findall(string)
    ]


def _card_date(card: Card, /, set_repo: SetRepository) -> str:
    return set_repo[card.set].release_date


def _card_compare(this: Card, other: Card, /, set_repo: SetRepository) -> int:
    this_date = _card_date(this, set_repo)
    other_date = _card_date(other, set_repo)

    if this_date != other_date:
        return 1 if this_date > other_date else -1

    this_chunks = _chunkify(this.number)
    other_chunks = _chunkify(other.number)

    if this_chunks != other_chunks:
        return 1 if this_chunks > other_chunks else -1

    return 0


@final
class CubeEntryMapper:

    def __init__(
        self, extra_card_repo: ExtraCardRepository, set_repo: SetRepository
    ) -> None:
        self._extra_card_repo = extra_card_repo
        self._set_repo = set_repo

    @translator
    def _fetch_oldest(self, name: str) -> Card:
        log.debug(f"Querying remote API for cards with '{name}'")

        cards = [
            card
            for card in Card.where(name=name).all()  # wtf ratelimit
            if card.name == name
            if card.set in self._set_repo
        ]

        log.debug(f"Returned {len(cards)} hits for cards with '{name}'")

        comparator = functools.partial(_card_compare, set_repo=self._set_repo)

        return min(cards, key=functools.cmp_to_key(comparator))

    def _obtain_from_api(self, entry: RawCubeEntry) -> CubeEntry:
        name, code, number = self._fetch_oldest(entry.name)

        return CubeEntry(name, number, code, entry.bucket)

    def _obtain_from_extra(self, entry: RawCubeEntry) -> CubeEntry:
        name, code, number = self._extra_card_repo[entry.name]

        return CubeEntry(name, number, code, entry.bucket)

    @functools.cache
    def map(self, entry: RawCubeEntry) -> CubeEntry:
        name = entry.name

        log.debug(f"Obtaining card '{name}'")

        card: CubeEntry

        if name in self._extra_card_repo:
            log.info(f"Fetching card '{name}' (source: this module)")
            card = self._obtain_from_extra(entry)
        else:
            log.info(f"Fetching card '{name}' (source: MTG API)")
            card = self._obtain_from_api(entry)

        log.info(
            f"Obtained card '{card.name}' "
            f"(set code: {card.set_code}, collector number: {card.number})"
        )

        return card


@final
class CubeScraper:

    def __init__(
        self, sanitizer: CardNameSanitizer, *, parser: str = "html.parser"
    ) -> None:
        self._sanitizer = sanitizer
        self._parser = parser

    @staticmethod
    def _get_name(content: Tag, /) -> str:
        tag = content.h1
        pattern = r"(?:Spotlight Cube Series ?[:-] )?(.*)"

        match = re.fullmatch(pattern, tag.text)
        value = match[1].removesuffix(" Cardlist") if match else "Cube"
        log.debug(f"Name: {value}")

        return value

    @staticmethod
    def _get_date(content: Tag, /) -> datetime:
        tag = content.find(id="content").find("p", class_="posted-in")
        pattern = r" [io]n "

        value = re.split(pattern, tag.text)[-1]
        log.debug(f"Date: {value}")

        return dateutil.parser.parse(value)

    @staticmethod
    def _get_author(content: Tag, /) -> str:
        tag = content.find(class_="author").p
        pattern = "By "

        value = tag.text.removeprefix(pattern)
        log.debug(f"Author: {value}")

        return value

    @staticmethod
    def _get_entries(
        content: Tag, /, sanitizer: CardNameSanitizer
    ) -> list[RawCubeEntry]:
        table = content.find_all("table", class_="sortable-table")[-1]
        rows = table.tbody.find_all("tr")

        elements = []

        for row in rows:
            first, second, *_ = row.find_all("td")

            name = sanitizer.sanitize(first.text)
            bucket = second.text

            elements.append(RawCubeEntry(name, bucket))

        return elements

    _SCRAPER_HEADERS: ClassVar = {
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": " ".join(
            [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "AppleWebKit/537.36 (KHTML, like Gecko)",
                "Chrome/79.0.3945.130",
                "Safari/537.36",
            ]
        ),
        "Accept": ",".join(
            [
                "text/html",
                "application/xhtml+xml",
                "application/xml;q=0.9",
                "image/webp",
                "image/apng",
                "*/*;q=0.8",
                "application/signed-exchange;v=b3;q=0.9",
            ]
        ),
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Referer": "https://duckduckgo.com/",
        "Accept-Encoding": ", ".join(["gzip", "deflate", "br"]),
        "Accept-Language": ",".join(["en-US,en-GB;q=0.9", "en;q=0.8"]),
    }

    @classmethod
    def _scrap(cls, url: Url) -> str:
        headers = cls._SCRAPER_HEADERS

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

        log.info(
            f"Cube data: {name} ({date:%d.%m.%Y}) by {author}, "
            f"{len(entries)} entries total"
        )

        return RawCube(name, date, author, entries)


@dataclasses.dataclass(frozen=True, order=True)
class RawCubeEntry:

    name: str
    bucket: str


@dataclasses.dataclass(frozen=True)
class RawCube:

    name: str
    date: datetime
    author: str
    entries: list[RawCubeEntry]

    def __iter__(self) -> Iterator[RawCubeEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


@dataclasses.dataclass(frozen=True, order=True)
class CubeEntry:

    name: str
    number: str  # can contain non-digits, sadly ('mb62sb', '221s★' etc.)
    set_code: str
    bucket: str


@dataclasses.dataclass(frozen=True)
class Cube:

    name: str
    date: datetime
    author: str
    entries: collections.Counter[CubeEntry]

    def __iter__(self) -> Iterator[Tuple[CubeEntry, int]]:
        return iter(self.entries.items())

    def __len__(self) -> int:
        return sum(self.entries.values())

    @classmethod
    def from_raw(cls, raw: RawCube, mapper: CubeEntryMapper) -> Cube:
        # TODO: should this be parallelized?
        cards = [mapper.map(entry) for entry in raw]

        return cls(raw.name, raw.date, raw.author, collections.Counter(cards))


def _export(raw: RawCube, file: StrPath, /, exporter: Type[Exporter]) -> None:
    mapper = CubeEntryMapper(ExtraCardRepository(), SetRepository())

    exporter().export(Cube.from_raw(raw, mapper), file)


URL = "https://magic.wizards.com/en/articles/archive/vintage-cube-cardlist"


def generate(file: StrPath, url: Optional[Url] = None) -> None:
    """Generate a *Magic Online* cube as an XMage deck file."""

    if url is None:
        url = URL

    with open(file, "a+", encoding="utf-8", errors="ignore") as stream:
        assert not stream.closed

    scrap = CubeScraper(CardNameSanitizer()).execute(url)

    _export(scrap, file, exporter=XMageExporter)


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


def _interrupt() -> NoReturn:
    _abort("interrupted by the user")


def _main(argv: Optional[list[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        epilog="example: %(prog)s deck.dck",
        fromfile_prefix_chars='@'
    )

    parser.add_argument("file", help="file to write cube contents to")
    parser.add_argument("--url", "-u", help="URL to scrap cube data from")
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
