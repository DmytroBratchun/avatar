"""Photos from Wikimedia Commons, for things a video talks about that the shows
never showed - the people who made them, voice actors, real places and people they
drew on. Commons is used because every file there has a known free licence (and
an author to credit), unlike images found by a web search.

Two ways in: the lead image of the Wikipedia article the query finds (usually the
right photo of a person, when it is on Commons - a non-free image that only
Wikipedia hosts is skipped), then Commons' own file search, which is noisy (a
search for "Mako" also finds sharks and streets) - which is why the editor has
Claude look at what was found before using any of it.
"""

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import unescape
from io import BytesIO
from pathlib import Path

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
# Wikimedia asks API clients to say who they are
USER_AGENT = "avatar-video-editor/0.1 (personal, local video editing tool)"
# photos are fetched at most this wide - the video is 1920 wide
FETCH_WIDTH = 1920
# smaller than this on its short side looks blurry on a 1080p screen
MIN_SIDE = 500
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
METADATA = "LicenseShortName|Artist|ImageDescription"


@dataclass
class Photo:
    title: str  # "File:Mako, 1986.jpg"
    url: str  # the file, scaled to at most FETCH_WIDTH wide
    width: int
    height: int
    license: str
    artist: str
    page_url: str  # the file's page on Commons, where its licence is stated

    @property
    def credit(self) -> str:
        name = re.sub(r"^File:|\.\w+$", "", self.title)
        by = f" by {self.artist}" if self.artist else ""
        return f'"{name}"{by}, {self.license or "see licence"}, via Wikimedia Commons ({self.page_url})'


def _get(api: str, params: dict) -> dict:
    query = urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    request = urllib.request.Request(f"{api}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _plain(html: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", html or ""))).strip()


def _photos(pages: list[dict]) -> list[Photo]:
    photos = []
    for page in pages:
        if page.get("missing") or not page.get("imageinfo"):
            continue
        info = page["imageinfo"][0]
        if info.get("mime") not in IMAGE_TYPES or min(info.get("width", 0), info.get("height", 0)) < MIN_SIDE:
            continue
        meta = info.get("extmetadata") or {}
        photos.append(
            Photo(
                title=page["title"],
                url=info.get("thumburl") or info["url"],
                width=info["width"],
                height=info["height"],
                license=_plain((meta.get("LicenseShortName") or {}).get("value")),
                artist=_plain((meta.get("Artist") or {}).get("value"))[:120],
                page_url=info.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page['title'])}",
            )
        )
    return photos


def _file_info(params: dict) -> list[Photo]:
    data = _get(COMMONS_API, {
        "action": "query", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": FETCH_WIDTH, "iiextmetadatafilter": METADATA, **params,
    })
    pages = sorted(data.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
    return _photos(pages)


def search(query: str, limit: int = 12) -> list[Photo]:
    """Photos for the query, the Wikipedia article's lead image first."""
    found: list[Photo] = []
    articles = _get(WIKIPEDIA_API, {
        "action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": 2, "prop": "pageimages", "piprop": "name",
    })
    lead = [
        "File:" + page["pageimage"]
        for page in sorted(articles.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
        if page.get("pageimage")
    ]
    if lead:
        # an image Wikipedia hosts itself (non-free) is "missing" on Commons and dropped
        found += _file_info({"titles": "|".join(lead)})
    found += _file_info({
        "generator": "search", "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit,
    })
    unique: dict[str, Photo] = {}
    for photo in found:
        unique.setdefault(photo.title, photo)
    return list(unique.values())[:limit]


def download(photo: Photo, folder: Path) -> Path:
    """Saves the photo as a JPEG in the folder (once) and returns its path."""
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{hashlib.sha1(photo.title.encode('utf-8')).hexdigest()[:16]}.jpg"
    if not target.exists():
        request = urllib.request.Request(photo.url, headers={"User-Agent": USER_AGENT})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = response.read()
                break
            except urllib.error.HTTPError as exc:
                # Wikimedia answers "too many requests" to a quick run of downloads
                if exc.code != 429 or attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        with Image.open(BytesIO(data)) as image:
            image = image.convert("RGB")
            partial = target.with_suffix(".part.jpg")
            image.save(partial, "JPEG", quality=92)
        partial.replace(target)
    return target
