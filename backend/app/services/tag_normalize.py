import re
from collections import Counter


def _normalize(tag: str) -> str:
    t = tag.strip().lower().replace("-", " ")
    return re.sub(r"\s+", " ", t)


def _group_key(tag: str) -> str:
    """Groups singular/plural variants of the same normalized tag together."""
    n = _normalize(tag)
    return n[:-1] if n.endswith("s") and len(n) > 3 else n


def build_canonical_map(all_tag_lists: list[list[str]]) -> dict[str, str]:
    """Given every scene's tag list for one field, return {raw_variant: canonical_form}
    for every raw string that isn't already canonical (i.e. only the ones that need fixing)."""
    counts: Counter[str] = Counter()
    for tags in all_tag_lists:
        counts.update(tags)

    groups: dict[str, list[str]] = {}
    for raw in counts:
        groups.setdefault(_group_key(raw), []).append(raw)

    mapping: dict[str, str] = {}
    for variants in groups.values():
        if len(variants) == 1:
            continue
        canonical = sorted(variants, key=lambda v: (-counts[v], len(v), v))[0]
        for v in variants:
            if v != canonical:
                mapping[v] = canonical
    return mapping


def apply_canonical_map(tags: list[str], mapping: dict[str, str]) -> list[str]:
    seen: list[str] = []
    for t in tags:
        canonical = mapping.get(t, t)
        if canonical not in seen:
            seen.append(canonical)
    return seen
