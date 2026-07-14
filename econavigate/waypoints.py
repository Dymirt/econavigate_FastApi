from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise
from typing import Any

Coordinate = list[float]
Point = dict[str, Any]
Route = dict[str, Any]

MAX_GREEN_WAYPOINTS = 4
MAX_ROUTE_CORRIDOR_METERS = 300.0
MIN_ROUTE_CORRIDOR_METERS = 70.0
CORRIDOR_DISTANCE_RATIO = 0.05
CLUSTER_CELL_METERS = 60.0
WAYPOINT_SPACING_METERS = 1_200.0


def _to_meters(coordinate: Coordinate) -> tuple[float, float]:
    longitude, latitude = coordinate
    return longitude * 67_800, latitude * 111_320


def _route_segment_grid(
    coordinates: list[Coordinate], corridor: float
) -> tuple[
    dict[
        tuple[int, int],
        list[tuple[tuple[float, float], tuple[float, float], float, float]],
    ],
    float,
    float,
]:
    projected = [_to_meters(coordinate) for coordinate in coordinates]
    cell_size = max(corridor, 100.0)
    grid: dict[
        tuple[int, int],
        list[tuple[tuple[float, float], tuple[float, float], float, float]],
    ] = defaultdict(list)
    distance_from_start = 0.0

    for start, end in pairwise(projected):
        segment_length = math.dist(start, end)
        min_x = math.floor((min(start[0], end[0]) - corridor) / cell_size)
        max_x = math.floor((max(start[0], end[0]) + corridor) / cell_size)
        min_y = math.floor((min(start[1], end[1]) - corridor) / cell_size)
        max_y = math.floor((max(start[1], end[1]) + corridor) / cell_size)
        segment = (start, end, distance_from_start, segment_length)
        for cell_x in range(min_x, max_x + 1):
            for cell_y in range(min_y, max_y + 1):
                grid[(cell_x, cell_y)].append(segment)
        distance_from_start += segment_length

    return grid, cell_size, distance_from_start


def _project_point(
    point: Point,
    segments: list[tuple[tuple[float, float], tuple[float, float], float, float]],
) -> tuple[float, float] | None:
    point_x, point_y = _to_meters([point["lon"], point["lat"]])
    best: tuple[float, float] | None = None

    for start, end, distance_from_start, segment_length in segments:
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length_squared = delta_x * delta_x + delta_y * delta_y
        ratio = 0.0
        if length_squared:
            ratio = max(
                0.0,
                min(
                    1.0,
                    ((point_x - start[0]) * delta_x + (point_y - start[1]) * delta_y)
                    / length_squared,
                ),
            )
        projected_x = start[0] + ratio * delta_x
        projected_y = start[1] + ratio * delta_y
        lateral_distance = math.hypot(point_x - projected_x, point_y - projected_y)
        progress = distance_from_start + ratio * segment_length
        if best is None or lateral_distance < best[0]:
            best = (lateral_distance, progress)

    return best


def select_green_waypoints(
    route: Route,
    greenery: list[Point],
    *,
    max_waypoints: int = MAX_GREEN_WAYPOINTS,
) -> list[dict[str, Any]]:
    """Choose ordered waypoints through dense tree clusters near a baseline route."""

    if max_waypoints < 1:
        return []

    corridor = min(
        MAX_ROUTE_CORRIDOR_METERS,
        max(MIN_ROUTE_CORRIDOR_METERS, float(route["distance"]) * CORRIDOR_DISTANCE_RATIO),
    )
    segment_grid, segment_cell_size, route_length = _route_segment_grid(
        route["geometry"]["coordinates"], corridor
    )
    if route_length <= 0:
        return []

    clusters: dict[tuple[int, int], dict[str, Any]] = {}
    for point in greenery:
        if point.get("type") != "tree":
            continue
        point_x, point_y = _to_meters([point["lon"], point["lat"]])
        segment_key = (
            math.floor(point_x / segment_cell_size),
            math.floor(point_y / segment_cell_size),
        )
        projection = _project_point(point, segment_grid.get(segment_key, []))
        if projection is None or projection[0] > corridor:
            continue
        lateral_distance, progress = projection
        if progress < route_length * 0.05 or progress > route_length * 0.95:
            continue

        cluster_key = (
            math.floor(point_x / CLUSTER_CELL_METERS),
            math.floor(point_y / CLUSTER_CELL_METERS),
        )
        cluster = clusters.setdefault(
            cluster_key,
            {
                "count": 0,
                "progressTotal": 0.0,
                "bestPoint": point,
                "bestDistance": lateral_distance,
            },
        )
        cluster["count"] += 1
        cluster["progressTotal"] += progress
        if lateral_distance < cluster["bestDistance"]:
            cluster["bestPoint"] = point
            cluster["bestDistance"] = lateral_distance

    candidates = []
    for cluster_key, cluster in clusters.items():
        density_count = sum(
            clusters.get(
                (cluster_key[0] + offset_x, cluster_key[1] + offset_y),
                {"count": 0},
            )["count"]
            for offset_x in range(-1, 2)
            for offset_y in range(-1, 2)
        )
        candidates.append(
            {
                **cluster,
                "densityCount": density_count,
                "progress": cluster["progressTotal"] / cluster["count"],
                "score": density_count * 10 - cluster["bestDistance"] / corridor,
            }
        )
    if not candidates:
        return []

    minimum_progress = min(candidate["progress"] for candidate in candidates)
    maximum_progress = max(candidate["progress"] for candidate in candidates)
    tree_covered_distance = max(0.0, maximum_progress - minimum_progress)
    waypoint_count = min(
        max_waypoints,
        max(1, math.ceil(tree_covered_distance / WAYPOINT_SPACING_METERS)),
    )
    bin_width = max(tree_covered_distance / waypoint_count, 1.0)
    bins: list[list[dict[str, Any]]] = [[] for _ in range(waypoint_count)]
    for candidate in candidates:
        bin_index = min(
            waypoint_count - 1,
            int((candidate["progress"] - minimum_progress) / bin_width),
        )
        bins[bin_index].append(candidate)

    selected = [
        max(bin_candidates, key=lambda candidate: candidate["score"])
        for bin_candidates in bins
        if bin_candidates
    ]
    selected.sort(key=lambda candidate: candidate["progress"])
    return [
        {
            "lat": candidate["bestPoint"]["lat"],
            "lon": candidate["bestPoint"]["lon"],
            "type": "through",
            "treeCount": candidate["densityCount"],
        }
        for candidate in selected
    ]
