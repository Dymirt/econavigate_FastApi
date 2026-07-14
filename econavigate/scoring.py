from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

Point = dict[str, Any]
Route = dict[str, Any]
Coordinate = list[float]
GREENERY_CORRIDOR_METERS = 5.0
FULL_SCORE_DENSITY_PER_KM = {"tree": 20.0, "shrub": 8.0, "forest": 2.0}
GREENERY_WEIGHTS = {"tree": 0.55, "shrub": 0.2, "forest": 0.25}


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


def _score_route(route: Route, counts: dict[str, int], has_greenery: bool) -> Route:
    if not has_greenery:
        return {**route, "greenScore": None, "sampleCount": 0}

    route_km = max(float(route["distance"]) / 1_000, 0.1)
    score = sum(
        min(counts[greenery_type] / route_km / full_score_density, 1.0)
        * GREENERY_WEIGHTS[greenery_type]
        for greenery_type, full_score_density in FULL_SCORE_DENSITY_PER_KM.items()
    )

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


def build_route_response(
    *,
    routes: list[Route],
    greenery: list[Point],
    from_place: dict[str, Any],
    to_place: dict[str, Any],
    mode: str,
    districts: list[str],
    warnings: list[str],
) -> dict[str, Any]:
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
        scored = _score_route(route, route_greenery["counts"], bool(corridor_points))
        detour_percent = _js_round(
            (route["distance"] - shortest_distance) / shortest_distance * 100
        )
        green_score = scored["greenScore"]
        rank_score = -detour_percent if green_score is None else green_score - detour_percent * 1.25
        scored_routes.append(
            {
                **scored,
                "detourPercent": detour_percent,
                "greenery": route_greenery["points"],
                "ecoCounts": route_greenery["counts"],
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
