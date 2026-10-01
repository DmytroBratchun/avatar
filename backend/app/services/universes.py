"""Universes: groups of shows and films set in the same fictional world.

Everything the prompts need to know about a particular world lives here as data -
its name, what it is, which shows belong to it, its regions, what its scenery looks
like - so the prompts themselves stay generic. Working with a new world means
adding one entry, not rewriting prompts.

The entries are kept in backend/data/universes.json (created from the built-in
defaults on first use) so they can be edited without touching code.
"""

import json
import threading

from pydantic import BaseModel, Field

from app.config import DATA_DIR

UNIVERSES_PATH = DATA_DIR / "universes.json"
_lock = threading.Lock()


class Universe(BaseModel):
    id: str
    name: str
    # one short paragraph telling the model what this world is
    description: str = ""
    # show_title values of the sources that belong to it
    shows: list[str] = Field(default_factory=list)
    # what this world calls a season ("Book", "Season", "Arc")
    season_word: str = "Season"
    # closed list a scene's region is chosen from; empty = regions are not used
    regions: list[str] = Field(default_factory=list)
    # rules for the cases the region list alone doesn't settle
    region_notes: str = ""
    # region -> what its scenery looks like, for choosing video backgrounds
    scenery: dict[str, str] = Field(default_factory=dict)


DEFAULT_UNIVERSES = [
    Universe(
        id="avatar",
        name="Avatar",
        description=(
            "The world of Avatar: The Last Airbender and its sequels: four nations built around bending "
            "the elements - the Water Tribes, the Earth Kingdom, the Fire Nation and the Air Nomads - "
            "and the Avatar, who can bend all four."
        ),
        shows=[
            "Avatar: The Last Airbender",
            "The Legend of Korra",
            "The Legend of Aang: The Last Airbender (2026 film)",
        ],
        season_word="Book",
        regions=[
            "Southern Water Tribe",
            "Northern Water Tribe",
            "Earth Kingdom",
            "Fire Nation",
            "Air Temples",
            "United Republic",
            "Spirit World",
            "Open sea or sky",
            "Unknown",
        ],
        region_notes=(
            "Choose by geography: Fire Nation colonies on Earth Kingdom land are Earth Kingdom. Republic "
            "City and Air Temple Island are United Republic. Kyoshi Island, the Foggy Swamp and the Si Wong "
            "Desert are Earth Kingdom. Use 'Open sea or sky' for travel far from land, on a ship or in the air."
        ),
        scenery={
            "Southern Water Tribe": "ice, snow, igloo villages and cold blue ocean",
            "Northern Water Tribe": "a city of ice with canals, snow walls and cold ocean",
            "Earth Kingdom": "walled cities with green tiled roofs, green valleys, rocky canyons and deserts",
            "Fire Nation": "volcanic islands, black rock, red-roofed cities and red skies",
            "Air Temples": "white temple towers on mountain peaks above the clouds, open sky",
            "United Republic": "a 1920s-style metropolis with towers, bridges, a harbour and airships",
            "Spirit World": "strange glowing forests, purple and green skies, floating shapes",
            "Open sea or sky": "open ocean, clouds and sky",
        },
    ),
]


def _load() -> list[Universe]:
    with _lock:
        if not UNIVERSES_PATH.exists():
            UNIVERSES_PATH.write_text(
                json.dumps([u.model_dump() for u in DEFAULT_UNIVERSES], indent=2, ensure_ascii=False), encoding="utf-8"
            )
        try:
            return [Universe.model_validate(u) for u in json.loads(UNIVERSES_PATH.read_text(encoding="utf-8"))]
        except (ValueError, OSError):
            # a broken edit shouldn't take captioning and the editor down with it
            return list(DEFAULT_UNIVERSES)


def all_universes() -> list[Universe]:
    return _load()


def get_universe(universe_id: str | None) -> Universe | None:
    return next((u for u in _load() if u.id == universe_id), None)


def universe_for_show(show_title: str | None) -> Universe:
    """The universe a show belongs to. A show nobody has assigned yet gets a
    bare universe of its own - no regions, no description - so nothing from another
    world leaks into its prompts."""
    for universe in _load():
        if show_title and show_title in universe.shows:
            return universe
    title = show_title or "this show"
    return Universe(id="", name=title, shows=[show_title] if show_title else [])
