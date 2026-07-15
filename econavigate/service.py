from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from .cache import PersistentTTLCache
from .config import Settings
from .errors import ApiError
from .green_areas import normalize_green_areas
from .models import CurrentLocation, RouteRequest
from .scoring import build_route_response
from .upstream import UpstreamClient
from .waypoints import build_green_corridor_waypoints

T = TypeVar("T")
MAX_GREEN_ROUTE_DETOUR_RATIO = 1.35
MAX_GREEN_ROUTE_EXTRA_METERS = 5_000.0

WARSAW_VIEWBOX = "20.8517,52.3681,21.2712,52.0978"
GREENERY_RESOURCES = {
    "tree": {
        "id": "ed6217dd-c8d0-4f7b-8bed-3b7eb81a95ba",
        "fields": "_id,x_wgs84,y_wgs84,gatunek,stan_zdrowia,dzielnica,adres",
    },
    "shrub": {
        "id": "0b1af81f-247d-4266-9823-693858ad5b5d",
        "fields": "_id,x_wgs84,y_wgs84,gatunek,stan_zdrowia,dzielnica,adres",
    },
    "forest": {
        "id": "75bedfd5-6c83-426b-9ae5-f03651857a48",
        "fields": "_id,x_wgs84,y_wgs84,dzielnica,obwód,osiedle,gat_panujacy,wiek",
    },
}
OVERPASS_GREEN_AREAS_QUERY = """[out:json][timeout:60];
(
  way["leisure"~"^(park|nature_reserve)$"](52.0978,20.8517,52.3681,21.2712);
  relation["leisure"~"^(park|nature_reserve)$"](52.0978,20.8517,52.3681,21.2712);
  way["landuse"~"^(village_green|recreation_ground|greenery)$"](52.0978,20.8517,52.3681,21.2712);
  relation["landuse"~"^(village_green|recreation_ground|greenery)$"](52.0978,20.8517,52.3681,21.2712);
);
out geom;
"""

DISTRICT_ALIASES = sorted(
    {
        "bemowo": "Bemowo",
        "bialoleka": "Białołęka",
        "bielany": "Bielany",
        "mokotow": "Mokotów",
        "ochota": "Ochota",
        "praga polnoc": "Praga Północ",
        "praga połnoc": "Praga Północ",
        "praga poludnie": "Praga Południe",
        "praga południe": "Praga Południe",
        "rembertow": "Rembertów",
        "srodmiescie": "Śródmieście",
        "targowek": "Targówek",
        "ursus": "Ursus",
        "ursynow": "Ursynów",
        "wawer": "Wawer",
        "wesola": "Wesoła",
        "wilanow": "Wilanów",
        "wlochy": "Włochy",
        "wola": "Wola",
        "zoliborz": "Żoliborz",
    }.items(),
    key=lambda entry: len(entry[0]),
    reverse=True,
)


def normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", str(value))
    without_marks = "".join(
        character for character in normalized if unicodedata.category(character) != "Mn"
    )
    return " ".join(without_marks.replace("-", " ").lower().split())


def resolve_district(address: dict[str, Any], display_name: str) -> str | None:
    candidates = [
        address.get("city_district"),
        address.get("suburb"),
        address.get("borough"),
        address.get("quarter"),
        display_name,
    ]
    normalized_candidates = [normalize_text(value) for value in candidates if value]
    for alias, official_name in DISTRICT_ALIASES:
        if any(alias in candidate for candidate in normalized_candidates):
            return official_name
    return None


def _normalize_place(result: dict[str, Any]) -> dict[str, Any]:
    display_name = str(result.get("display_name", ""))
    label = ",".join(display_name.split(",")[:4]).strip()
    return {
        "lat": float(result["lat"]),
        "lon": float(result["lon"]),
        "label": label,
        "district": resolve_district(result.get("address") or {}, display_name),
    }


def _normalize_greenery_record(greenery_type: str, record: dict[str, Any]) -> dict[str, Any] | None:
    try:
        longitude = float(record["x_wgs84"])
        latitude = float(record["y_wgs84"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(longitude) and math.isfinite(latitude)):
        return None

    if greenery_type == "forest":
        age = record.get("wiek")
        return {
            "id": f"{greenery_type}-{record.get('_id')}",
            "type": greenery_type,
            "lon": longitude,
            "lat": latitude,
            "district": record.get("dzielnica"),
            "name": record.get("gat_panujacy") or record.get("obwód") or "Forest area",
            "detail": f"{age} years" if age else (record.get("osiedle") or ""),
        }

    return {
        "id": f"{greenery_type}-{record.get('_id')}",
        "type": greenery_type,
        "lon": longitude,
        "lat": latitude,
        "district": record.get("dzielnica"),
        "name": record.get("gatunek") or ("Tree" if greenery_type == "tree" else "Shrub"),
        "detail": record.get("stan_zdrowia") or record.get("adres") or "",
    }


class NominatimRateLimiter:
    """Serialize Nominatim calls and respect its public-server request interval."""

    def __init__(self, minimum_interval_seconds: float) -> None:
        self._minimum_interval = minimum_interval_seconds
        self._next_request_at = 0.0
        self._lock = asyncio.Lock()

    async def run(self, request: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            delay = self._next_request_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request_at = time.monotonic() + self._minimum_interval
            return await request()


class EcoService:
    def __init__(
        self,
        settings: Settings,
        upstream: UpstreamClient,
        cache: PersistentTTLCache,
    ) -> None:
        self.settings = settings
        self.upstream = upstream
        self.cache = cache
        self.nominatim = NominatimRateLimiter(settings.nominatim_min_interval_seconds)

    async def get_air_quality(self) -> dict[str, Any]:
        token = self.settings.warsaw_token
        if not token:
            raise ApiError("WARSAW_API_TOKEN is not configured on the server.", 503)

        async def load() -> dict[str, Any]:
            payload = await self.upstream.get_json(
                f"{self.settings.warsaw_api_url}/air_sensors_get/",
                source="Warsaw air-quality service",
                params={"apikey": token},
            )
            stations = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(stations, list):
                raise ApiError("Warsaw air-quality data had an unexpected format.", 502)
            return {
                "stations": stations,
                "fetchedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }

        return await self.cache.get_or_load(
            "v1:air-quality", self.settings.air_cache_ttl_seconds, load
        )

    async def build_green_route(self, request: RouteRequest) -> dict[str, Any]:
        origin_fingerprint = (
            normalize_text(request.from_query)
            if isinstance(request.from_query, str)
            else f"{request.from_query.lat:.5f},{request.from_query.lon:.5f}"
        )
        fingerprint = "|".join(
            (
                origin_fingerprint,
                normalize_text(request.to_query),
                request.mode,
            )
        )
        digest = hashlib.sha256(fingerprint.encode()).hexdigest()
        return await self.cache.get_or_load(
            f"v10:route:{digest}",
            self.settings.route_cache_ttl_seconds,
            lambda: self._build_green_route(request),
        )

    async def _build_green_route(self, request: RouteRequest) -> dict[str, Any]:
        from_place, to_place = await asyncio.gather(
            self._resolve_origin(request.from_query),
            self._geocode(request.to_query),
        )
        baseline_result, greenery_result, green_area_result = await asyncio.gather(
            self._fetch_routes(from_place, to_place, request.mode),
            self._fetch_greenery(),
            self._fetch_green_areas(),
            return_exceptions=True,
        )
        if isinstance(baseline_result, BaseException):
            raise baseline_result
        if isinstance(greenery_result, BaseException):
            greenery, warnings = [], ["Complete greenery inventories were unavailable"]
        else:
            greenery, warnings = greenery_result
        if isinstance(green_area_result, BaseException):
            green_area_data = {"areaCount": 0, "cells": []}
            warnings.append("Park and green-area polygons were unavailable")
        else:
            green_area_data = green_area_result

        baseline_routes = baseline_result
        green_areas = green_area_data["cells"]
        districts = sorted({str(point["district"]) for point in greenery if point.get("district")})
        inventory_counts = {
            greenery_type: sum(point["type"] == greenery_type for point in greenery)
            for greenery_type in GREENERY_RESOURCES
        }
        inventory_counts["greenArea"] = green_area_data["areaCount"]
        fastest_route = min(baseline_routes, key=lambda route: route["distance"])
        green_waypoints = await asyncio.to_thread(
            build_green_corridor_waypoints,
            fastest_route,
            greenery,
            green_areas,
        )
        routes = baseline_routes
        routing_strategy = "ranked-alternatives"
        if green_waypoints:
            try:
                generated_route = (
                    await self._fetch_routes(
                        from_place,
                        to_place,
                        request.mode,
                        waypoints=green_waypoints,
                        alternates=0,
                        route_kind="green-corridor",
                    )
                )[0]
            except ApiError:
                green_waypoints = []
            else:
                maximum_green_route_distance = min(
                    fastest_route["distance"] * MAX_GREEN_ROUTE_DETOUR_RATIO,
                    fastest_route["distance"] + MAX_GREEN_ROUTE_EXTRA_METERS,
                )
                if generated_route["distance"] <= maximum_green_route_distance:
                    routes = [generated_route, *baseline_routes[:2]]
                    routing_strategy = "citywide-green-corridor"
                else:
                    green_waypoints = []

        response = await asyncio.to_thread(
            build_route_response,
            routes=routes,
            greenery=greenery,
            green_areas=green_areas,
            from_place=from_place,
            to_place=to_place,
            mode=request.mode,
            districts=districts,
            warnings=warnings,
        )
        return {
            **response,
            "routingStrategy": routing_strategy,
            "greenWaypoints": green_waypoints,
            "inventoryCounts": inventory_counts,
        }

    async def _resolve_origin(self, origin: str | CurrentLocation) -> dict[str, Any]:
        if isinstance(origin, str):
            return await self._geocode(origin)

        try:
            resolved = await self._reverse_geocode([origin.lon, origin.lat])
        except ApiError:
            resolved = {"district": None}
        return {
            **resolved,
            "lat": origin.lat,
            "lon": origin.lon,
            "label": origin.label,
        }

    async def _geocode(self, query: str) -> dict[str, Any]:
        digest = hashlib.sha256(normalize_text(query).encode()).hexdigest()

        async def load() -> dict[str, Any]:
            async def request() -> Any:
                return await self.upstream.get_json(
                    f"{self.settings.nominatim_api_url}/search",
                    source="Address search",
                    params={
                        "q": f"{query}, Warszawa",
                        "format": "jsonv2",
                        "addressdetails": "1",
                        "limit": "1",
                        "countrycodes": "pl",
                        "viewbox": WARSAW_VIEWBOX,
                        "bounded": "1",
                    },
                )

            results = await self.nominatim.run(request)
            if not isinstance(results, list) or not results:
                raise ApiError(f"Could not find “{query}” in Warsaw.", 404)
            return _normalize_place(results[0])

        return await self.cache.get_or_load(
            f"v1:geocode:{digest}", self.settings.geocode_cache_ttl_seconds, load
        )

    async def _reverse_geocode(self, coordinate: list[float]) -> dict[str, Any]:
        longitude, latitude = coordinate
        key = f"v1:reverse:{latitude:.3f}:{longitude:.3f}"

        async def load() -> dict[str, Any]:
            async def request() -> Any:
                return await self.upstream.get_json(
                    f"{self.settings.nominatim_api_url}/reverse",
                    source="District lookup",
                    params={
                        "lat": str(latitude),
                        "lon": str(longitude),
                        "format": "jsonv2",
                        "addressdetails": "1",
                        "zoom": "10",
                    },
                )

            result = await self.nominatim.run(request)
            if not isinstance(result, dict):
                raise ApiError("District lookup returned an unexpected format.", 502)
            return _normalize_place(result)

        return await self.cache.get_or_load(key, self.settings.geocode_cache_ttl_seconds, load)

    async def _fetch_routes(
        self,
        from_place: dict[str, Any],
        to_place: dict[str, Any],
        mode: str,
        *,
        waypoints: list[dict[str, Any]] | None = None,
        alternates: int = 2,
        route_kind: str = "alternative",
    ) -> list[dict[str, Any]]:
        costing = "bicycle" if mode == "cycling" else "pedestrian"
        locations = [{"lat": from_place["lat"], "lon": from_place["lon"]}]
        locations.extend(
            {
                "lat": waypoint["lat"],
                "lon": waypoint["lon"],
                "type": "through",
            }
            for waypoint in (waypoints or [])
        )
        locations.append({"lat": to_place["lat"], "lon": to_place["lon"]})
        request: dict[str, Any] = {
            "locations": locations,
            "costing": costing,
            "format": "osrm",
            "shape_format": "geojson",
            "language": "en-US",
            "directions_type": "none",
        }
        if alternates > 0:
            request["alternates"] = alternates
        if costing == "bicycle":
            request["costing_options"] = {"bicycle": {"bicycle_type": "city", "use_roads": 0.2}}

        payload = await self.upstream.get_json(
            self.settings.valhalla_api_url,
            source="Route service",
            params={"json": json.dumps(request, separators=(",", ":"))},
        )
        route_records = payload.get("routes") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("code") != "Ok"
            or not isinstance(route_records, list)
            or not route_records
        ):
            raise ApiError("No walkable or cyclable route was found.", 404)

        routes = []
        for index, route in enumerate(route_records):
            route_id = (
                "green-corridor"
                if route_kind == "green-corridor" and index == 0
                else f"route-{index + 1}"
            )
            routes.append(
                {
                    "id": route_id,
                    "distance": float(route["distance"]),
                    "duration": float(route["duration"]),
                    "summary": (
                        "Citywide green corridor"
                        if route_kind == "green-corridor"
                        else (route.get("legs") or [{}])[0].get("summary") or "Warsaw route"
                    ),
                    "geometry": route["geometry"],
                    "routeKind": route_kind,
                    "greenWaypoints": waypoints or [],
                }
            )
        return routes

    async def _fetch_green_areas(self) -> dict[str, Any]:
        async def load() -> dict[str, Any]:
            last_error: ApiError | None = None
            for url in self.settings.overpass_url_list:
                try:
                    payload = await self.upstream.get_json(
                        url,
                        source="OpenStreetMap green-area data",
                        params={"data": OVERPASS_GREEN_AREAS_QUERY},
                    )
                    return await asyncio.to_thread(normalize_green_areas, payload)
                except (ApiError, ValueError) as error:
                    last_error = error if isinstance(error, ApiError) else ApiError(str(error), 502)
            raise last_error or ApiError("No Overpass API URL is configured.", 502)

        return await self.cache.get_or_load(
            "v1:green-areas:osm",
            self.settings.greenery_cache_ttl_seconds,
            load,
        )

    async def _fetch_greenery_resource(self, greenery_type: str) -> list[dict[str, Any]]:
        resource = GREENERY_RESOURCES[greenery_type]
        key = f"v3:greenery:all:{greenery_type}"

        async def load() -> list[dict[str, Any]]:
            normalized_records: list[dict[str, Any]] = []
            offset = 0
            while True:
                params = {
                    "resource_id": resource["id"],
                    "fields": resource["fields"],
                    "sort": "_id asc",
                    "limit": str(self.settings.greenery_page_size),
                    "offset": str(offset),
                }
                for attempt in range(2):
                    try:
                        payload = await self.upstream.get_json(
                            f"{self.settings.warsaw_api_url}/datastore_search/",
                            source=f"Warsaw {greenery_type} data",
                            params=params,
                        )
                        break
                    except ApiError:
                        if attempt == 1:
                            raise
                        await asyncio.sleep(0.25)
                result = payload.get("result") if isinstance(payload, dict) else None
                records = result.get("records") if isinstance(result, dict) else None
                if not isinstance(records, list):
                    raise ApiError("Warsaw greenery data had an unexpected format.", 502)
                normalized_records.extend(
                    point
                    for record in records
                    if (point := _normalize_greenery_record(greenery_type, record)) is not None
                )
                offset += len(records)
                total = int(result.get("total") or offset)
                if not records or offset >= total:
                    break
            return normalized_records

        return await self.cache.get_or_load(key, self.settings.greenery_cache_ttl_seconds, load)

    async def _fetch_greenery(self) -> tuple[list[dict[str, Any]], list[str]]:
        greenery_types = list(GREENERY_RESOURCES)
        results = await asyncio.gather(
            *(self._fetch_greenery_resource(greenery_type) for greenery_type in greenery_types),
            return_exceptions=True,
        )
        points_by_id: dict[str, dict[str, Any]] = {}
        warnings = []
        for greenery_type, result in zip(greenery_types, results, strict=False):
            if isinstance(result, BaseException):
                warnings.append(f"Complete {greenery_type} data was unavailable")
            else:
                points_by_id.update((point["id"], point) for point in result)
        return list(points_by_id.values()), warnings
