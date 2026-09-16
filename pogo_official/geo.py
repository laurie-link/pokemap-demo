"""Parse coordinates and geodesy helpers."""
from __future__ import annotations

import math
import re
from typing import Tuple


def parse_lat_lng(text: str) -> tuple[float, float]:
    raw = (text or "").strip().replace("，", ",")
    if not raw:
        raise ValueError("请输入经纬度")
    if "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        parts = [p for p in re.split(r"\s+", raw) if p]
    if len(parts) != 2:
        raise ValueError("格式应为：纬度, 经度（例如 25.033964, 121.564468）")
    return float(parts[0]), float(parts[1])


EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bbox_from_radius(
    lat: float, lng: float, radius_m: float
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Axis-aligned bounding box that contains the circle (ne, sw)."""
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    cos_lat = math.cos(math.radians(lat))
    if abs(cos_lat) < 1e-9:
        dlng = 180.0
    else:
        dlng = math.degrees(radius_m / (EARTH_RADIUS_M * cos_lat))
    ne = (lat + dlat, lng + dlng)
    sw = (lat - dlat, lng - dlng)
    return ne, sw
