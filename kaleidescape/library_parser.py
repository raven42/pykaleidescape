"""Bounded, read-only parsing of the local browser interface; never execute JS."""

# pylint: disable=missing-function-docstring,redefined-builtin,too-many-locals,too-many-statements

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from .error import KaleidescapeError


class LibraryFormatError(KaleidescapeError):
    """A local browser page was incomplete or had an unsupported layout."""


MAX_TITLES = 10_000
MAX_COLLECTIONS = 200
VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def malformed() -> LibraryFormatError:
    """Do not disclose source pages, addresses, or metadata in errors."""
    return LibraryFormatError("The library page format was not recognized")


def clean(value: str, limit: int = 500) -> str:
    """Bound untrusted text while retaining Unicode metadata."""
    value = " ".join(value.split())
    if len(value) > limit or "\x00" in value:
        raise malformed()
    return value


def identity(value: str) -> str:
    """Reject malformed identifiers instead of normalizing their raw identity."""
    if not value or len(value) > 256 or any(c.isspace() or ord(c) < 32 for c in value):
        raise malformed()
    return value


@dataclass
class Node:
    """Small structural tree backed by Python's HTML parser."""

    tag: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)
    closed: bool = False

    def nodes(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.nodes()

    def find(self, *, id=None, cls=None, tag=None):
        return next(
            (
                node
                for node in self.nodes()
                if (id is None or node.attrs.get("id") == id)
                and (cls is None or cls in node.attrs.get("class", "").split())
                and (tag is None or node.tag == tag)
            ),
            None,
        )

    def text(self) -> str:
        if self.tag in {"script", "style"}:
            return ""
        return " ".join(
            child.text() if isinstance(child, Node) else child
            for child in self.children
        )


class Tree(HTMLParser):
    """Keep depth/node limits even for malformed or hostile HTML."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.count = 0
        if len(source) > 8 * 1024 * 1024:
            raise malformed()
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 200_000 or len(self.stack) > 64:
            raise malformed()
        node = Node(tag, {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)
        else:
            node.closed = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack[index].closed = True
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def catalog(source: str) -> tuple[list[dict], list[str] | None]:
    """Validate the entire listing before any missing titles become unavailable."""
    root = Tree(source).root
    count_node = root.find(id="movie_count_str")
    if count_node is None or root.find(tag="table") is None:
        raise malformed()
    count_text = clean(count_node.text()).replace(",", "")
    match = re.fullmatch(r"(\d+|No)(?:\s+movies?)?", count_text, re.IGNORECASE)
    if not match:
        raise malformed()
    expected = 0 if match[1].lower() == "no" else int(match[1])
    rows: dict[str, dict] = {}
    groups: set[str] = set()
    for node in root.nodes():
        if (
            node.tag != "tr"
            or "movie_container" not in node.attrs.get("class", "").split()
        ):
            continue
        handle = identity(node.attrs.get("selection_handle", ""))
        group = identity(node.attrs.get("selection_group", ""))
        title = node.find(cls="movie_title")
        if not handle or not group or title is None or not clean(title.text()):
            raise malformed()
        groups.add(group)

        def cell(cls, node=node):
            value = node.find(cls=cls)
            return clean(value.text()) if value else ""

        edition = cell("movie_media_type")
        known = [edition] if edition and edition.casefold() != "multiple" else []
        if handle in rows:
            if rows[handle]["source_group"] != group:
                raise malformed()
            formats = sorted(
                set(rows[handle]["formats"]) | set(known), key=str.casefold
            )
            if len(formats) > 100:
                raise malformed()
            rows[handle]["formats"] = formats
            if formats:
                rows[handle]["edition"] = formats[0]
            continue  # A summary row and its concrete format may share a handle.
        year = cell("movie_year")
        rows[handle] = {
            "source_handle": handle,
            "source_group": group,
            "title": clean(title.text()),
            "rating": cell("movie_rating"),
            "directors": names(cell("movie_director")),
            "genres": names(cell("movie_genre")),
            "year": int(year)
            if year.isascii() and year.isdigit() and len(year) == 4
            else None,
            "edition": edition,
            "formats": known,
        }
        if len(rows) > MAX_TITLES:
            raise malformed()
    if len(groups) != expected or expected > MAX_TITLES:
        raise malformed()
    collections = set()
    pulldown = root.find(id="collection_pulldown")
    if pulldown is None:
        return list(rows.values()), None
    for node in pulldown.nodes():
        if node.tag != "a":
            continue
        url = urlsplit(node.attrs.get("href", ""))
        if url.scheme or url.netloc or url.path not in {"/movies", "movies"}:
            continue
        values = parse_qs(url.query).get("collection", [])
        if len(values) == 1 and (name := clean(values[0], 256)):
            # The built-in Scenes menu opens scene management, not a title
            # collection. Parsing it as Movies aborts every collection refresh.
            if name.casefold() == "scenes":
                continue
            collections.add(name)
    if len(collections) > MAX_COLLECTIONS:
        raise malformed()
    if not {"played", "paused"}.issubset(name.casefold() for name in collections):
        return list(rows.values()), None
    return list(rows.values()), sorted(collections)


def names(value: str) -> list[str]:
    """Retain only provided names, never fabricate absent credits."""
    values = list(
        dict.fromkeys(
            clean(part) for part in re.split(r"[,;\n]", value) if part.strip()
        )
    )
    if len(values) > 100:
        raise malformed()
    return values


def artwork_path(value: str) -> str | None:
    """Only the observed read-only cover directories, without URLs or queries."""
    if len(value) > 1024:
        return None
    if not re.fullmatch(
        r"/webcovers(?:large)?/[A-Za-z0-9_./-]+\.(?:jpg|jpeg|png|webp)",
        value,
        re.IGNORECASE,
    ):
        return None
    if any(part in {"", ".", ".."} for part in value.split("/")[1:]):
        return None
    return value


def detail_collections(source: str) -> tuple[str, list[str]]:
    """Read the explicit per-version collection field without executing links."""
    root = Tree(source).root
    title = root.find(id="movie_title")
    if title is None or not clean(title.text()):
        raise malformed()
    values = []
    for row in root.nodes():
        if row.tag != "tr":
            continue
        label, value = row.find(cls="details_name"), row.find(cls="details_value")
        if (
            label
            and value
            and clean(label.text()).rstrip(":").casefold()
            == ("in the following collections")
        ):
            if not row.closed or not value.closed:
                raise malformed()
            values.append(value)
    if len(values) != 1:
        raise malformed()
    links = [node for node in values[0].nodes() if node.tag == "a"]
    if (
        not links
        or len(links) > MAX_COLLECTIONS
        or not all(link.closed for link in links)
    ):
        raise malformed()
    result = [clean(link.text(), 256) for link in links]
    if not all(result) or len(set(result)) != len(result):
        raise malformed()
    return clean(title.text()), result


def details(source: str) -> dict:
    """Read known labeled fields and covers; ignore forms/editing routes/scripts."""
    root = Tree(source).root
    title = root.find(id="movie_title")
    if title is None or not clean(title.text()):
        raise malformed()
    result: dict[str, object] = {
        "actors": [],
        "writers": [],
        "studios": [],
        "synopsis": "",
        "duration_seconds": None,
        "artwork_path": None,
    }
    labels = {
        "cast": "actors",
        "actors": "actors",
        "director": "directors",
        "directors": "directors",
        "writer": "writers",
        "writers": "writers",
        "written by": "writers",
        "studio": "studios",
        "studios": "studios",
        "genres": "genres",
    }
    for row in root.nodes():
        if row.tag != "tr":
            continue
        label, value = row.find(cls="details_name"), row.find(cls="details_value")
        if label is None or value is None:
            continue
        name = clean(label.text()).rstrip(":").casefold()
        if name in labels:
            result[labels[name]] = names(clean(value.text(), 20_000))
        elif name == "synopsis":
            result["synopsis"] = clean(value.text(), 20_000)
    misc = root.find(id="misc_text")
    if misc and (
        match := re.search(
            r"\b(\d+(?:\.\d+)?)\s*min(?:ute)?s?\b", misc.text(), re.IGNORECASE
        )
    ):
        minutes = float(match[1])
        if 0 <= minutes <= 1440:
            result["duration_seconds"] = minutes * 60
    cover = root.find(id="cover_art")
    image = cover.find(tag="img") if cover else None
    if image:
        result["artwork_path"] = artwork_path(image.attrs.get("src", ""))
    return result
