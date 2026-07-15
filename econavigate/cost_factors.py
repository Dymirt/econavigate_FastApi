from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise
from typing import Any

from .green_areas import area_grid_key, green_area_grid

Coordinate = list[float]
Route = dict[str, Any]
Point = dict[str, Any]

FACTOR_BY_AREA = {
    "park": 0.12,
    "nature_reserve": 0.18,
    "recreation_ground": 0.24,
    "green_area": 0.32,
}
POINT_FACTOR = {"tree": 0.34, "forest": 0.38, "shrub": 0.44}
SAMPLE_SPACING_METERS = 20.0
POINT_CORRIDOR_METERS = 5.0
POINT_GRID_METERS = 25.0
MIN_FACTOR_LINE_METERS = 12.0
MAX_LINEAR_FACTORS = 96
MAX_PREFERRED_FACTORS = 32
ROAD_PENALTY_FACTOR = 50.0


def _to_meters(coordinate: Coordinate) -> tuple[float, float]:
    return coordinate[0] * 67_800, coordinate[1] * 111_320


def _distance(start: Coordinate, end: Coordinate) -> float:
    start_x, start_y = _to_meters(start)
    end_x, end_y = _to_meters(end)
    return math.hypot(end_x - start_x, end_y - start_y)


def _interpolate(start: Coordinate, end: Coordinate, ratio: float) -> Coordinate:
    return [
        start[0] + (end[0] - start[0]) * ratio,
        start[1] + (end[1] - start[1]) * ratio,
    ]


def _point_grid(points: list[Point]) -> dict[tuple[int, int], list[Point]]:
    grid: dict[tuple[int, int], list[Point]] = defaultdict(list)
    for point in points:
        x, y = _to_meters([point["lon"], point["lat"]])
        grid[(math.floor(x / POINT_GRID_METERS), math.floor(y / POINT_GRID_METERS))].append(point)
    return grid


def _point_to_segment_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    length_squared = delta_x * delta_x + delta_y * delta_y
    ratio = 0.0
    if length_squared:
        ratio = max(
            0.0,
            min(
                1.0,
                ((point[0] - start[0]) * delta_x + (point[1] - start[1]) * delta_y)
                / length_squared,
            ),
        )
    return math.hypot(
        point[0] - (start[0] + ratio * delta_x),
        point[1] - (start[1] + ratio * delta_y),
    )


def _nearby_point_factor(
    start: Coordinate,
    end: Coordinate,
    grid: dict[tuple[int, int], list[Point]],
) -> float | None:
    start_m = _to_meters(start)
    end_m = _to_meters(end)
    minimum_x = math.floor((min(start_m[0], end_m[0]) - POINT_CORRIDOR_METERS) / POINT_GRID_METERS)
    maximum_x = math.floor((max(start_m[0], end_m[0]) + POINT_CORRIDOR_METERS) / POINT_GRID_METERS)
    minimum_y = math.floor((min(start_m[1], end_m[1]) - POINT_CORRIDOR_METERS) / POINT_GRID_METERS)
    maximum_y = math.floor((max(start_m[1], end_m[1]) + POINT_CORRIDOR_METERS) / POINT_GRID_METERS)
    factor: float | None = None
    for cell_x in range(minimum_x, maximum_x + 1):
        for cell_y in range(minimum_y, maximum_y + 1):
            for point in grid.get((cell_x, cell_y), []):
                if (
                    _point_to_segment_distance(
                        _to_meters([point["lon"], point["lat"]]), start_m, end_m
                    )
                    <= POINT_CORRIDOR_METERS
                ):
                    point_factor = POINT_FACTOR.get(str(point.get("type")), 0.5)
                    factor = point_factor if factor is None else min(factor, point_factor)
    return factor


def _factor_for_segment(
    start: Coordinate,
    end: Coordinate,
    areas: dict[tuple[int, int], dict[str, Any]],
    points: dict[tuple[int, int], list[Point]],
) -> float | None:
    midpoint = _interpolate(start, end, 0.5)
    area = areas.get(area_grid_key(midpoint))
    area_factor = FACTOR_BY_AREA.get(str(area.get("category"))) if area else None
    point_factor = _nearby_point_factor(start, end, points)
    factors = [factor for factor in (area_factor, point_factor) if factor is not None]
    return min(factors) if factors else None


def _sample_route_segments(route: Route) -> list[tuple[Coordinate, Coordinate]]:
    sampled: list[tuple[Coordinate, Coordinate]] = []
    coordinates = route["geometry"]["coordinates"]
    for start, end in pairwise(coordinates):
        segment_length = _distance(start, end)
        pieces = max(1, math.ceil(segment_length / SAMPLE_SPACING_METERS))
        for index in range(pieces):
            sampled.append(
                (
                    _interpolate(start, end, index / pieces),
                    _interpolate(start, end, (index + 1) / pieces),
                )
            )
    return sampled


def build_linear_cost_factors(
    routes: list[Route],
    greenery: list[Point],
    green_areas: list[dict[str, Any]],
    *,
    penalized_routes: list[Route] | None = None,
) -> list[dict[str, Any]]:
    """Create Valhalla edge-cost discounts from already matched green route segments.

    The input routes are probes. Their shapes lie on Valhalla's routing graph, so the
    final request can match these lines back to real edges without forcing waypoints.
    """

    areas = green_area_grid(green_areas)
    points = _point_grid(greenery)
    lines: list[tuple[float, float, list[Coordinate]]] = []
    seen: set[tuple[float, tuple[tuple[float, float], ...]]] = set()

    for route in routes:
        current_factor: float | None = None
        current_coordinates: list[Coordinate] = []

        def flush() -> None:
            nonlocal current_factor, current_coordinates
            if current_factor is not None and len(current_coordinates) >= 2:
                length = sum(_distance(start, end) for start, end in pairwise(current_coordinates))
                signature = (
                    current_factor,
                    tuple(
                        (round(point[0], 6), round(point[1], 6)) for point in current_coordinates
                    ),
                )
                if length >= MIN_FACTOR_LINE_METERS and signature not in seen:
                    seen.add(signature)
                    lines.append((current_factor, length, current_coordinates))
            current_factor = None
            current_coordinates = []

        for start, end in _sample_route_segments(route):
            factor = _factor_for_segment(start, end, areas, points)
            if factor is None:
                flush()
                continue
            if current_factor is None or not math.isclose(current_factor, factor):
                flush()
                current_factor = factor
                current_coordinates = [start, end]
            else:
                current_coordinates.append(end)
        flush()

    # Penalize every known non-green escape route heavily. The service calls this
    # repeatedly with newly discovered alternatives, while discounts remain useful
    # on Valhalla deployments that allow factors below one.
    for route in penalized_routes or []:
        current_coordinates: list[Coordinate] = []

        def flush_penalty() -> None:
            nonlocal current_coordinates
            if len(current_coordinates) >= 2:
                length = sum(_distance(start, end) for start, end in pairwise(current_coordinates))
                signature = (
                    ROAD_PENALTY_FACTOR,
                    tuple(
                        (round(point[0], 6), round(point[1], 6)) for point in current_coordinates
                    ),
                )
                if length >= MIN_FACTOR_LINE_METERS and signature not in seen:
                    seen.add(signature)
                    lines.append((ROAD_PENALTY_FACTOR, length, current_coordinates))
            current_coordinates = []

        for start, end in _sample_route_segments(route):
            if _factor_for_segment(start, end, areas, points) is not None:
                flush_penalty()
            elif not current_coordinates:
                current_coordinates = [start, end]
            else:
                current_coordinates.append(end)
        flush_penalty()

    preferred = sorted(
        (line for line in lines if line[0] < 1), key=lambda item: (item[0], -item[1])
    )[:MAX_PREFERRED_FACTORS]
    penalties = sorted((line for line in lines if line[0] > 1), key=lambda item: -item[1])[
        : MAX_LINEAR_FACTORS - len(preferred)
    ]
    selected_lines = [*preferred, *penalties]
    return [
        {
            "type": "Feature",
            "properties": {"factor": factor},
            "geometry": {"type": "LineString", "coordinates": coordinates},
        }
        for factor, _, coordinates in selected_lines
    ]


def route_retrace_ratio(route: Route, *, cell_size_meters: float = 5.0) -> float:
    """Return the share of route edges traversed more than once in either direction."""

    cells: list[tuple[int, int]] = []
    for start, end in _sample_route_segments(route):
        length = _distance(start, end)
        pieces = max(1, math.ceil(length / cell_size_meters))
        for index in range(pieces + 1):
            x, y = _to_meters(_interpolate(start, end, index / pieces))
            cell = (round(x / cell_size_meters), round(y / cell_size_meters))
            if not cells or cells[-1] != cell:
                cells.append(cell)

    edges = [tuple(sorted((start, end))) for start, end in pairwise(cells) if start != end]
    if not edges:
        return 0.0
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    repeated = 0
    for edge in edges:
        if edge in seen:
            repeated += 1
        else:
            seen.add(edge)
    return repeated / len(edges)
