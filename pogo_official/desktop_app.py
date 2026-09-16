"""Desktop app for the official Pokémon GO web map."""
from __future__ import annotations

import subprocess
import sys
from typing import Any

from pogo_official.client import DROP_TYPES, OfficialMapClient, OfficialMapError, count_entities
from pogo_official.server import app_url, ensure_server
from pogo_official.geo import bbox_from_radius, parse_lat_lng


def _clipboard_text() -> str:
    if sys.platform == "darwin":
        try:
            return subprocess.check_output(["pbpaste"], text=True)
        except Exception:
            return ""
    if sys.platform == "win32":
        try:
            return subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                text=True,
            )
        except Exception:
            return ""
    try:
        return subprocess.check_output(["xclip", "-selection", "clipboard", "-o"], text=True)
    except Exception:
        return ""


class DesktopApi:
    def __init__(self) -> None:
        self._client = OfficialMapClient()

    def get_clipboard(self) -> str:
        return _clipboard_text()

    def search(self, coords: str, radius: str, region: str, types: Any = None) -> dict[str, Any]:
        try:
            lat, lng = parse_lat_lng(coords)
            radius_m = float(str(radius).strip())
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        circular = region == "circle"
        allowed = set(DROP_TYPES) | {"EVENT"}
        drop_types = None
        if types:
            cleaned = tuple(str(item) for item in types if str(item) in allowed)
            if cleaned:
                drop_types = cleaned
        try:
            pois = self._client.nearby(
                lat, lng, radius_m, circular_filter=circular, drop_types=drop_types
            )
        except OfficialMapError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        ne, sw = bbox_from_radius(lat, lng, radius_m)
        return {
            "ok": True,
            "lat": lat,
            "lng": lng,
            "radius_m": radius_m,
            "region": region,
            "counts": count_entities(pois),
            "pois": pois,
            "bbox": {"ne": list(ne), "sw": list(sw)},
        }


def main() -> None:
    import webview

    ensure_server()
    webview.create_window(
        "Pokémon GO Map",
        app_url(),
        js_api=DesktopApi(),
        width=1280,
        height=860,
        min_size=(900, 600),
    )
    webview.start()
