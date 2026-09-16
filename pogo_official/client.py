"""Client for the official pokemongo.com / Campfire map GraphQL API."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
from typing import Any, Optional

import requests

from pogo_official.s2cover import cover_radius
from pogo_official.geo import bbox_from_radius, haversine_m

GRAPHQL_URL = "https://niantic-social-api.nianticlabs.com/public/graphql"
REALITY_CHANNEL_ID = "da83476a-c4da-4312-a610-a4f2fc2c37f0"
SOURCE_NAME = "PGO"
DEFAULT_CELL_LEVEL = 16
CHUNK_SIZE = 12
POWERSPOT_CHUNK_SIZE = 12
CHUNK_WORKERS = 8
MAX_RETRIES = 2
RETRY_BACKOFF_S = 0.25
POWERSPOT_EMPTY_RETRIES = 0
EVENT_CELL_LEVEL = 12
TRANSIENT_MARKERS = (
    "HTTP Mapping: 400",
    "GET_MAP_OBJECTS_FOR_CAMPFIRE",
    "Bad Request",
    "UNAVAILABLE",
    "DEADLINE_EXCEEDED",
)

MAP_QUERY = """
query PgoGameMapObjectsByS2CellsProvider_mapObjectsByS2Cells_Query(
  $realityChannelMapObjectsByS2CellsInput: RealityChannelMapObjectsByS2CellsInput!
) {
  realityChannelMapObjectsByS2Cells(input: $realityChannelMapObjectsByS2CellsInput) {
    mapObjectsByS2CellsAndTypes {
      s2CellId
      mapObjectsByType {
        type
        mapObjects {
          id
          mapObjectType
          score
          pgoGym {
            location { latitude longitude }
            isMegaEnhancedEligible
            team
            raid {
              bossName
              rating
              bossImageUrl
              eggImageUrl
              startTime
              hatchTime
              endTime
              megaEnrageShieldCount
            }
          }
          pgoPowerspot {
            location { latitude longitude }
            maxBattle { bossName rating bossImageUrl }
            overrideMaxBattle { bossName rating openTime bossImageUrl }
            overrideBattleStartMinutes
            overrideBattleEndMinutes
          }
          pgoPokestop {
            location { latitude longitude }
          }
          pgoRoute {
            name
            distanceMeters
            durationSeconds
            reversible
            startPoi {
              location { latitude longitude }
              imageUrl
              fortId
            }
            endPoi {
              location { latitude longitude }
              fortId
            }
          }
          event {
            name
            address
            location
            eventTime
            eventEndTime
            badgeGrants
            mapObjectLocation { latitude longitude }
            campfireLiveEvent { eventType eventName id }
          }
        }
      }
    }
  }
}
"""

POWER_QUERY = """
query PgoGameMapObjectsByS2CellsProvider_mapObjectsByS2Cells_Query(
  $realityChannelMapObjectsByS2CellsInput: RealityChannelMapObjectsByS2CellsInput!
) {
  realityChannelMapObjectsByS2Cells(input: $realityChannelMapObjectsByS2CellsInput) {
    mapObjectsByS2CellsAndTypes {
      s2CellId
      mapObjectsByType {
        type
        mapObjects {
          id
          mapObjectType
          score
          pgoPowerspot {
            location { latitude longitude }
            maxBattle { bossName rating bossImageUrl }
            overrideMaxBattle { bossName rating openTime bossImageUrl }
            overrideBattleStartMinutes
            overrideBattleEndMinutes
          }
        }
      }
    }
  }
}
"""

GYM_STOP_TYPES = ("PGO_GYM", "PGO_POKESTOP")
POWERSPOT_TYPES = ("PGO_POWERSPOT",)
ROUTE_TYPES = ("PGO_ROUTE",)
EVENT_TYPES = ("CA_EVENT",)
DROP_TYPES = GYM_STOP_TYPES + POWERSPOT_TYPES + ROUTE_TYPES + EVENT_TYPES
ROUTE_CHUNK_SIZE = 40
EVENT_CHUNK_SIZE = 12
TYPE_MAP = {
    "PGO_POKESTOP": "POKESTOP",
    "PGO_GYM": "GYM",
    "PGO_POWERSPOT": "POWERSPOT",
    "PGO_ROUTE": "ROUTE",
    "CA_EVENT": "EVENT",
    "EVENT": "EVENT",
}
GMAX_RATINGS = {"BREAD_DOUGH_BATTLE_LEVEL_1"}
MEGA_RAID_RATINGS = {"6", "7", "16", "17"}
MEGA_ENHANCED_RATINGS = {"16", "17"}


class OfficialMapError(Exception):
    """GraphQL or transport failure."""


def _rating_key(rating: Any) -> str:
    return str(rating or "").strip()


def _is_gmax_rating(rating: Any) -> bool:
    key = _rating_key(rating)
    return key in GMAX_RATINGS or "DOUGH" in key.upper()


def _is_mega_raid_rating(rating: Any) -> bool:
    key = _rating_key(rating)
    if key in MEGA_RAID_RATINGS:
        return True
    return "MEGA" in key.upper()


def _is_mega_enhanced_rating(rating: Any) -> bool:
    key = _rating_key(rating)
    if key in MEGA_ENHANCED_RATINGS:
        return True
    return "MEGA_ENHANCED" in key.upper() or "SUPER_MEGA" in key.upper()


def _raid_info(raid: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not raid:
        return None
    if not any(
        raid.get(k)
        for k in ("bossName", "eggImageUrl", "bossImageUrl", "startTime", "hatchTime", "endTime")
    ):
        return None
    rating = raid.get("rating")
    return {
        "bossName": raid.get("bossName"),
        "rating": rating,
        "bossImageUrl": raid.get("bossImageUrl"),
        "eggImageUrl": raid.get("eggImageUrl"),
        "startTime": raid.get("startTime"),
        "hatchTime": raid.get("hatchTime"),
        "endTime": raid.get("endTime"),
        "megaEnrageShieldCount": raid.get("megaEnrageShieldCount") or 0,
        "mega": _is_mega_raid_rating(rating),
        "megaEnhanced": _is_mega_enhanced_rating(rating),
    }


def _max_battle(spot: dict[str, Any]) -> Optional[dict[str, Any]]:
    battle = spot.get("overrideMaxBattle") or spot.get("maxBattle")
    if not battle or not (battle.get("bossName") or battle.get("bossImageUrl")):
        return None
    rating = battle.get("rating")
    gmax = _is_gmax_rating(rating)
    return {
        "bossName": battle.get("bossName"),
        "rating": rating,
        "bossImageUrl": battle.get("bossImageUrl"),
        "openTime": battle.get("openTime"),
        "gmax": gmax,
        "dmax": not gmax,
    }


def _path_points(raw: Optional[list[Any]]) -> list[list[float]]:
    out: list[list[float]] = []
    for point in raw or []:
        if not isinstance(point, dict):
            continue
        lat, lng = point.get("latitude"), point.get("longitude")
        if lat is None or lng is None:
            continue
        out.append([float(lat), float(lng)])
    return out


def _normalize(obj: dict[str, Any]) -> Optional[dict[str, Any]]:
    raw_type = obj.get("mapObjectType")
    entity = TYPE_MAP.get(raw_type or "")
    loc = None
    extra: dict[str, Any] = {}
    if raw_type == "PGO_GYM" and obj.get("pgoGym"):
        gym = obj["pgoGym"]
        loc = gym.get("location")
        raid = _raid_info(gym.get("raid"))
        mega_eligible = bool(gym.get("isMegaEnhancedEligible"))
        extra = {
            "team": gym.get("team"),
            "megaEligible": mega_eligible,
            "raid": raid,
            "superMega": mega_eligible or bool(raid and (raid.get("mega") or raid.get("megaEnhanced"))),
        }
    elif raw_type == "PGO_POKESTOP" and obj.get("pgoPokestop"):
        loc = obj["pgoPokestop"].get("location")
    elif raw_type == "PGO_POWERSPOT" and obj.get("pgoPowerspot"):
        spot = obj["pgoPowerspot"]
        loc = spot.get("location")
        battle = _max_battle(spot)
        extra = {
            "maxBattle": battle,
            "gmax": bool(battle and battle.get("gmax")),
            "dmax": bool(battle and battle.get("dmax")),
        }
    elif raw_type == "PGO_ROUTE" and obj.get("pgoRoute"):
        route = obj["pgoRoute"]
        start = (route.get("startPoi") or {}).get("location")
        end = (route.get("endPoi") or {}).get("location")
        loc = start
        path = _path_points(route.get("locationList"))
        if not path and start and end:
            path = [
                [start["latitude"], start["longitude"]],
                [end["latitude"], end["longitude"]],
            ]
        extra = {
            "name": route.get("name"),
            "distanceMeters": route.get("distanceMeters"),
            "durationSeconds": route.get("durationSeconds"),
            "reversible": bool(route.get("reversible")),
            "endLat": (end or {}).get("latitude"),
            "endLng": (end or {}).get("longitude"),
            "path": path,
        }
    elif raw_type in {"CA_EVENT", "EVENT"} and obj.get("event"):
        event = obj["event"]
        loc = event.get("mapObjectLocation")
        live = event.get("campfireLiveEvent") or {}
        event_type = live.get("eventType") or ""
        extra = {
            "name": event.get("name") or live.get("eventName"),
            "address": event.get("address") or event.get("location"),
            "eventTime": event.get("eventTime"),
            "eventEndTime": event.get("eventEndTime"),
            "eventType": event_type,
            "superMega": str(event_type).upper() == "SUPERMEGAS",
        }
    if not entity or not loc:
        return None
    return {
        "id": obj.get("id"),
        "entity": entity,
        "lat": loc["latitude"],
        "lng": loc["longitude"],
        "score": obj.get("score") or 0,
        **extra,
    }


def _is_transient(exc: BaseException) -> bool:
    text = str(exc)
    return any(marker in text for marker in TRANSIENT_MARKERS)


def _sources_for(drop_types: tuple[str, ...]) -> list[dict[str, Any]]:
    # Official map sends one source entry per drop type, not a combined list.
    return [{"name": SOURCE_NAME, "dropTypes": [drop_type]} for drop_type in drop_types]


class OfficialMapClient:
    def __init__(self, *, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s
        self._local = threading.local()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://pokemongo.com",
                "Referer": "https://pokemongo.com/en/map",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "x-graphql-operation-name": "PgoGameMapObjectsByS2CellsProvider_mapObjectsByS2Cells_Query",
            }
        )

    def _http(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self.session.headers)
            self._local.session = session
        return session

    def _post_once(self, variables: dict[str, Any], *, query: str = MAP_QUERY) -> dict[str, Any]:
        res = self._http().post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables},
            timeout=self.timeout_s,
        )
        if res.status_code >= 400:
            raise OfficialMapError(f"HTTP {res.status_code}: {res.text[:300]}")
        payload = res.json()
        if payload.get("errors"):
            msg = payload["errors"][0].get("message") if payload["errors"] else "GraphQL error"
            raise OfficialMapError(str(msg))
        return payload.get("data") or {}

    def _post(self, variables: dict[str, Any], *, query: str = MAP_QUERY) -> dict[str, Any]:
        last: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                return self._post_once(variables, query=query)
            except (OfficialMapError, requests.RequestException) as exc:
                last = exc
                if not _is_transient(exc) or attempt == MAX_RETRIES - 1:
                    raise OfficialMapError(str(exc)) from exc
                time.sleep(RETRY_BACKOFF_S * (2**attempt))
        raise OfficialMapError(str(last) if last else "request failed")

    def _fetch_chunk(
        self,
        cell_ids: list[int],
        *,
        cell_level: int,
        drop_types: tuple[str, ...],
        query: str = MAP_QUERY,
    ) -> list[dict[str, Any]]:
        if not cell_ids:
            return []
        data = self._post(
            {
                "realityChannelMapObjectsByS2CellsInput": {
                    "realityChannelId": REALITY_CHANNEL_ID,
                    "s2CellLevel": cell_level,
                    "sourcesByS2Cells": [
                        {
                            "s2CellId": str(cid),
                            "sources": _sources_for(drop_types),
                        }
                        for cid in cell_ids
                    ],
                }
            },
            query=query,
        )
        rows = (
            (data.get("realityChannelMapObjectsByS2Cells") or {}).get(
                "mapObjectsByS2CellsAndTypes"
            )
            or []
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            for group in row.get("mapObjectsByType") or []:
                for obj in group.get("mapObjects") or []:
                    normalized = _normalize(obj)
                    if normalized:
                        out.append(normalized)
        return out

    def _fetch_chunk_split(
        self,
        cell_ids: list[int],
        *,
        cell_level: int,
        drop_types: tuple[str, ...],
        query: str = MAP_QUERY,
    ) -> list[dict[str, Any]]:
        try:
            return self._fetch_chunk(
                cell_ids, cell_level=cell_level, drop_types=drop_types, query=query
            )
        except OfficialMapError as exc:
            if len(cell_ids) == 1 or not _is_transient(exc):
                if len(cell_ids) == 1:
                    return []
                raise
            mid = len(cell_ids) // 2
            left = self._fetch_chunk_split(
                cell_ids[:mid], cell_level=cell_level, drop_types=drop_types, query=query
            )
            right = self._fetch_chunk_split(
                cell_ids[mid:], cell_level=cell_level, drop_types=drop_types, query=query
            )
            return left + right

    def _fetch_cells_once(
        self,
        cell_ids: list[int],
        *,
        cell_level: int,
        drop_types: tuple[str, ...],
        chunk_size: int = CHUNK_SIZE,
        query: str = MAP_QUERY,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        size = max(1, chunk_size)
        chunks = [cell_ids[i : i + size] for i in range(0, len(cell_ids), size)]
        if not chunks:
            return []
        workers = min(CHUNK_WORKERS, len(chunks))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    self._fetch_chunk_split,
                    chunk,
                    cell_level=cell_level,
                    drop_types=drop_types,
                    query=query,
                )
                for chunk in chunks
            ]
            for fut in as_completed(futures):
                for poi in fut.result():
                    oid = poi.get("id")
                    if not oid or oid in seen:
                        continue
                    seen.add(str(oid))
                    out.append(poi)
        return out

    def _fetch_powerspots(
        self,
        cell_ids: list[int],
        *,
        cell_level: int,
    ) -> list[dict[str, Any]]:
        last: list[dict[str, Any]] = []
        for attempt in range(POWERSPOT_EMPTY_RETRIES + 1):
            last = self._fetch_cells_once(
                cell_ids,
                cell_level=cell_level,
                drop_types=POWERSPOT_TYPES,
                chunk_size=POWERSPOT_CHUNK_SIZE,
                query=POWER_QUERY,
            )
            if last:
                return last
            if attempt < POWERSPOT_EMPTY_RETRIES:
                time.sleep(1.5)
        return last

    def _merge_unique(
        self, base: list[dict[str, Any]], extra: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        seen = {str(poi.get("id")) for poi in base if poi.get("id")}
        for poi in extra:
            oid = poi.get("id")
            if not oid or str(oid) in seen:
                continue
            seen.add(str(oid))
            base.append(poi)
        return base

    def fetch_cells(
        self,
        cell_ids: list[int],
        *,
        cell_level: int = DEFAULT_CELL_LEVEL,
        drop_types: tuple[str, ...] = DROP_TYPES,
    ) -> list[dict[str, Any]]:
        # Official map uses GAME_MAP_SEPARATE_CONTENT_API_CALLS. Power Spots,
        # Routes, and Events must not ride along with the dense gym/stop GMO
        # call or they get dropped.
        wanted = set(drop_types)
        jobs: list[tuple] = []
        if "PGO_POWERSPOT" in wanted:
            jobs.append(("power", lambda: self._fetch_powerspots(cell_ids, cell_level=cell_level)))
        if "PGO_ROUTE" in wanted:
            jobs.append(
                (
                    "route",
                    lambda: self._fetch_cells_once(
                        cell_ids,
                        cell_level=cell_level,
                        drop_types=ROUTE_TYPES,
                        chunk_size=ROUTE_CHUNK_SIZE,
                    ),
                )
            )
        others = tuple(
            drop
            for drop in drop_types
            if drop not in {"PGO_POWERSPOT", "PGO_ROUTE", "CA_EVENT", "EVENT"}
        )
        if others:
            jobs.append(
                (
                    "gym_stop",
                    lambda: self._fetch_cells_once(
                        cell_ids, cell_level=cell_level, drop_types=others
                    ),
                )
            )
        if "CA_EVENT" in wanted or "EVENT" in wanted:
            jobs.append(
                (
                    "event",
                    lambda: self._fetch_cells_once(
                        cell_ids,
                        cell_level=cell_level,
                        drop_types=EVENT_TYPES,
                        chunk_size=EVENT_CHUNK_SIZE,
                    ),
                )
            )
        pois: list[dict[str, Any]] = []
        if not jobs:
            return pois
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = [pool.submit(fn) for _, fn in jobs]
            for fut in as_completed(futures):
                pois = self._merge_unique(pois, fut.result())
        return pois

    def nearby(
        self,
        lat: float,
        lng: float,
        radius_m: float,
        *,
        circular_filter: bool = False,
        cell_level: int = DEFAULT_CELL_LEVEL,
        drop_types: Optional[tuple[str, ...]] = None,
    ) -> list[dict[str, Any]]:
        wanted = tuple(drop_types) if drop_types else DROP_TYPES
        cells = cover_radius(lat, lng, radius_m, cell_level)
        if not cells:
            return []
        poi_types = tuple(drop for drop in wanted if drop not in {"CA_EVENT", "EVENT"})
        event_wanted = any(drop in wanted for drop in ("CA_EVENT", "EVENT"))
        event_cells = (
            cover_radius(lat, lng, radius_m, EVENT_CELL_LEVEL, extra_ring=1)
            if event_wanted
            else []
        )
        jobs = []
        if poi_types:
            jobs.append(
                lambda: self.fetch_cells(cells, cell_level=cell_level, drop_types=poi_types)
            )
        if event_cells:
            jobs.append(
                lambda: self.fetch_cells(
                    event_cells, cell_level=EVENT_CELL_LEVEL, drop_types=EVENT_TYPES
                )
            )
        pois: list[dict[str, Any]] = []
        if len(jobs) == 1:
            pois = jobs[0]()
        elif jobs:
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                for fut in as_completed([pool.submit(fn) for fn in jobs]):
                    pois = self._merge_unique(pois, fut.result())
        ne, sw = bbox_from_radius(lat, lng, radius_m)
        out: list[dict[str, Any]] = []
        for poi in pois:
            plat, plng = poi["lat"], poi["lng"]
            dist = haversine_m(lat, lng, plat, plng)
            poi["distanceM"] = round(dist, 1)
            # Campfire events are city-scale pins (Raid Hour / Spotlight Hour),
            # not clipped to the Stop/Gym search circle.
            if poi.get("entity") == "EVENT":
                out.append(poi)
                continue
            if not (sw[0] <= plat <= ne[0] and sw[1] <= plng <= ne[1]):
                continue
            if circular_filter and dist > radius_m:
                continue
            out.append(poi)
        out.sort(key=lambda p: p["distanceM"])
        return out


def count_entities(pois: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    raids = 0
    super_mega = 0
    gmax = 0
    dmax = 0
    battles = 0
    for poi in pois:
        counts[poi["entity"]] += 1
        if poi.get("raid"):
            raids += 1
        if poi.get("superMega"):
            super_mega += 1
        if poi.get("gmax"):
            gmax += 1
        if poi.get("dmax"):
            dmax += 1
        if poi.get("maxBattle"):
            battles += 1
    return {
        "POKESTOP": counts.get("POKESTOP", 0),
        "GYM": counts.get("GYM", 0),
        "POWERSPOT": counts.get("POWERSPOT", 0),
        "ROUTE": counts.get("ROUTE", 0),
        "EVENT": counts.get("EVENT", 0),
        "RAID": raids,
        "SUPER_MEGA": super_mega,
        "GMAX": gmax,
        "DMAX": dmax,
        "MAX_BATTLE": battles,
    }
