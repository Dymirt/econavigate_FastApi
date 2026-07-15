from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

from .green_areas import AREA_CELL_SIZE_METERS, PARK_WEIGHT, area_grid_key, green_area_grid

Point = dict[str, Any]
Route = dict[str, Any]
Coordinate = list[float]
GREENERY_CORRIDOR_METERS = 5.0
FULL_SCORE_DENSITY_PER_KM = {"tree": 20.0, "shrub": 8.0, "forest": 2.0}
GREENERY_WEIGHTS = {"tree": 0.55, "shrub": 0.2, "forest": 0.25}
GREEN_AREA_SCORE_WEIGHT = 0.55
FULL_GREEN_AREA_ROUTE_RATIO = 0.35
AREA_SAMPLE_SPACING_METERS = AREA_CELL_SIZE_METERS / 2


def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def to_meters(coordinate: Coordinate) -> tuple[float, float]:
    lon, lat = coordinate
    return lon * 67_800, lat * 111_320


def route_bounds(routes: list[Route], padding: float = 0.003) -> dict[str, float]:
    coordinates = [
        coordinate for route in routes for coordinate in route["geometry"]["coordinates"]
    ]
    longitudes = [coordinate[0] for coordinate in coordinates]
    latitudes = [coordinate[1] for coordinate in coordinates]
    return {
        "west": min(longitudes) - padding,
        "south": min(latitudes) - padding,
        "east": max(longitudes) + padding,
        "north": max(latitudes) + padding,
    }


def _score_route(
    route: Route,
    counts: dict[str, int],
    area_coverage: dict[str, Any],
    has_greenery: bool,
) -> Route:
    if not has_greenery:
        return {**route, "greenScore": None, "sampleCount": 0}

    route_km = max(float(route["distance"]) / 1_000, 0.1)
    score = sum(
        min(counts[greenery_type] / route_km / full_score_density, 1.0)
        * GREENERY_WEIGHTS[greenery_type]
        for greenery_type, full_score_density in FULL_SCORE_DENSITY_PER_KM.items()
    )
    area_score = min(
        area_coverage["weightedMeters"] / (route_km * 1_000) / FULL_GREEN_AREA_ROUTE_RATIO,
        1.0,
    )
    score = min(score + area_score * GREEN_AREA_SCORE_WEIGHT, 1.0)

    return {
        **route,
        "greenScore": _js_round(score * 100),
        "sampleCount": len(route["geometry"]["coordinates"]),
    }


def _point_to_segment_distance_projected(
    point_x: float,
    point_y: float,
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    start_x, start_y = start
    end_x, end_y = end
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    length_squared = delta_x * delta_x + delta_y * delta_y
    projection = 0.0
    if length_squared:
        projection = max(
            0.0,
            min(
                1.0,
                ((point_x - start_x) * delta_x + (point_y - start_y) * delta_y) / length_squared,
            ),
        )
    return math.hypot(
        point_x - (start_x + projection * delta_x),
        point_y - (start_y + projection * delta_y),
    )


def _build_segment_grid(
    coordinates: list[Coordinate],
    *,
    cell_size: float = 40,
    padding: float = GREENERY_CORRIDOR_METERS,
) -> tuple[
    dict[tuple[int, int], list[tuple[tuple[float, float], tuple[float, float]]]],
    float,
]:
    projected = [to_meters(coordinate) for coordinate in coordinates]
    grid: dict[tuple[int, int], list[tuple[tuple[float, float], tuple[float, float]]]] = (
        defaultdict(list)
    )

    for start, end in pairwise(projected):
        min_x = math.floor((min(start[0], end[0]) - padding) / cell_size)
        max_x = math.floor((max(start[0], end[0]) + padding) / cell_size)
        min_y = math.floor((min(start[1], end[1]) - padding) / cell_size)
        max_y = math.floor((max(start[1], end[1]) + padding) / cell_size)
        for cell_x in range(min_x, max_x + 1):
            for cell_y in range(min_y, max_y + 1):
                grid[(cell_x, cell_y)].append((start, end))
    return grid, cell_size


def _select_route_greenery(points: list[Point], route: Route) -> dict[str, Any]:
    segment_grid, cell_size = _build_segment_grid(route["geometry"]["coordinates"])
    nearby: list[Point] = []

    for point in points:
        point_x, point_y = to_meters([point["lon"], point["lat"]])
        key = (math.floor(point_x / cell_size), math.floor(point_y / cell_size))
        if any(
            _point_to_segment_distance_projected(point_x, point_y, start, end)
            <= GREENERY_CORRIDOR_METERS
            for start, end in segment_grid.get(key, [])
        ):
            nearby.append(point)

    counts = {"tree": 0, "shrub": 0, "forest": 0}
    for point in nearby:
        counts[point["type"]] += 1

    return {"points": nearby, "counts": counts}


def _measure_green_area_coverage(
    route: Route, area_grid: dict[tuple[int, int], dict[str, Any]]
) -> dict[str, Any]:
    green_meters = 0.0
    park_meters = 0.0
    weighted_meters = 0.0
    area_names: set[str] = set()
    park_names: set[str] = set()

    for start, end in pairwise(route["geometry"]["coordinates"]):
        start_x, start_y = to_meters(start)
        end_x, end_y = to_meters(end)
        length = math.hypot(end_x - start_x, end_y - start_y)
        sample_count = max(1, math.ceil(length / AREA_SAMPLE_SPACING_METERS))
        sample_length = length / sample_count
        for index in range(sample_count):
            ratio = (index + 0.5) / sample_count
            coordinate = [
                (start_x + (end_x - start_x) * ratio) / 67_800,
                (start_y + (end_y - start_y) * ratio) / 111_320,
            ]
            area = area_grid.get(area_grid_key(coordinate))
            if not area:
                continue
            green_meters += sample_length
            weighted_meters += sample_length * float(area["weight"]) / PARK_WEIGHT
            name = area.get("name")
            if name:
                area_names.add(str(name))
            if area["category"] == "park":
                park_meters += sample_length
                if name:
                    park_names.add(str(name))

    route_distance = max(float(route["distance"]), 1.0)
    return {
        "greenMeters": _js_round(green_meters),
        "parkMeters": _js_round(park_meters),
        "percent": _js_round(min(green_meters / route_distance, 1.0) * 100),
        "areas": sorted(area_names),
        "parks": sorted(park_names),
        "weightedMeters": weighted_meters,
    }


def build_route_response(
    *,
    routes: list[Route],
    greenery: list[Point],
    green_areas: list[dict[str, Any]] | None = None,
    from_place: dict[str, Any],
    to_place: dict[str, Any],
    mode: str,
    districts: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    green_areas = green_areas or []
    area_grid = green_area_grid(green_areas)
    bounds = route_bounds(routes)
    corridor_points = [
        point
        for point in greenery
        if bounds["west"] <= point["lon"] <= bounds["east"]
        and bounds["south"] <= point["lat"] <= bounds["north"]
    ]
    shortest_distance = min(route["distance"] for route in routes)
    scored_routes: list[Route] = []

    for route in routes:
        route_greenery = _select_route_greenery(corridor_points, route)
        area_coverage = _measure_green_area_coverage(route, area_grid)
        scored = _score_route(
            route,
            route_greenery["counts"],
            area_coverage,
            bool(corridor_points or area_grid),
        )
        detour_percent = _js_round(
            (route["distance"] - shortest_distance) / shortest_distance * 100
        )
        green_score = scored["greenScore"]
        rank_score = (
            -detour_percent if green_score is None else green_score * 1_000 - detour_percent
        )
        scored_routes.append(
            {
                **scored,
                "detourPercent": detour_percent,
                "greenery": route_greenery["points"],
                "ecoCounts": route_greenery["counts"],
                "greenAreaCoverage": {
                    key: value for key, value in area_coverage.items() if key != "weightedMeters"
                },
                "_rankScore": rank_score,
            }
        )

    selected = max(scored_routes, key=lambda route: route["_rankScore"])
    public_routes = [
        {key: value for key, value in route.items() if key != "_rankScore"}
        for route in scored_routes
    ]

    selected_route = next(route for route in public_routes if route["id"] == selected["id"])

    return {
        "from": from_place,
        "to": to_place,
        "mode": mode,
        "districts": districts,
        "selectedRouteId": selected["id"],
        "routes": public_routes,
        "greenery": selected_route["greenery"],
        "ecoCounts": selected_route["ecoCounts"],
        "warnings": warnings,
        "calculatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
