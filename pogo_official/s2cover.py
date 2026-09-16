"""S2 cell covering for the official Pokémon GO map API."""
from __future__ import annotations

import s2cell

from pogo_official.geo import bbox_from_radius


def cover_radius(
    lat: float,
    lng: float,
    radius_m: float,
    level: int,
    *,
    extra_ring: int = 0,
) -> list[int]:
    """Return S2 cell IDs at `level` whose centers fall in a padded bbox."""
    ne, sw = bbox_from_radius(lat, lng, radius_m * 1.15)
    north, east = ne
    south, west = sw
    start = s2cell.lat_lon_to_cell_id(lat, lng, level)
    seen: set[int] = set()
    stack = [start]
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        clat, clng = s2cell.cell_id_to_lat_lon(cid)
        if not (south <= clat <= north and west <= clng <= east):
            continue
        seen.add(cid)
        stack.extend(s2cell.cell_id_to_neighbor_cell_ids(cid))
    extra = set(seen)
    for _ in range(max(0, extra_ring)):
        ring: set[int] = set()
        for cid in extra:
            ring.update(s2cell.cell_id_to_neighbor_cell_ids(cid))
        extra = ring - seen
        seen.update(extra)
    return sorted(seen)
