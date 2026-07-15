from __future__ import annotations

import heapq
import math
from typing import Any

from .green_areas import area_cell_center

Coordinate = list[float]
GridKey = tuple[int, int]
Point = dict[str, Any]
Route = dict[str, Any]

CELL_SIZE_METERS = 120.0
MAX_GREEN_WAYPOINTS = 8
WAYPOINT_SPACING_METERS = 900.0
BARREN_AREA_PENALTY = 6.0
GREEN_SATURATION_SCORE = 14.0
SEARCH_DETOUR_RATIO = 1.8
SEARCH_EXTRA_METERS = 5_000.0
WARSAW_GREEN_BOUNDS = {
    "west": 20.8517,
    "south": 52.0978,
    "east": 21.2712,
    "north": 52.3681,
}
GREENERY_WEIGHTS = {"tree": 1.0, "shrub": 0.6, "forest": 10.0}
REPRESENTATIVE_PRIORITY = {"tree": 3, "shrub": 2, "forest": 1}


def _to_meters(coordinate: Coordinate) -> tuple[float, float]:
    longitude, latitude = coordinate
    return longitude * 67_800, latitude * 111_320


def _from_meters(x: float, y: float) -> Coordinate:
    return [x / 67_800, y / 111_320]


def _grid_key(coordinate: Coordinate) -> GridKey:
    x, y = _to_meters(coordinate)
    return math.floor(x / CELL_SIZE_METERS), math.floor(y / CELL_SIZE_METERS)


def _cell_center(key: GridKey) -> Coordinate:
    return _from_meters(
        (key[0] + 0.5) * CELL_SIZE_METERS,
        (key[1] + 0.5) * CELL_SIZE_METERS,
    )


def _inside_warsaw(coordinate: Coordinate) -> bool:
    longitude, latitude = coordinate
    return (
        WARSAW_GREEN_BOUNDS["west"] <= longitude <= WARSAW_GREEN_BOUNDS["east"]
        and WARSAW_GREEN_BOUNDS["south"] <= latitude <= WARSAW_GREEN_BOUNDS["north"]
    )


def _empty_grid_cell() -> dict[str, Any]:
    return {
        "weightedCount": 0.0,
        "areaBonus": 0.0,
        "greenArea": None,
        "counts": {"tree": 0, "shrub": 0, "forest": 0},
        "representative": None,
    }


def _build_green_grid(
    points: list[Point], green_areas: list[dict[str, Any]]
) -> dict[GridKey, dict[str, Any]]:
    grid: dict[GridKey, dict[str, Any]] = {}
    for point in points:
        greenery_type = point.get("type")
        if greenery_type not in GREENERY_WEIGHTS:
            continue
        coordinate = [point["lon"], point["lat"]]
        if not _inside_warsaw(coordinate):
            continue
        key = _grid_key(coordinate)
        cell = grid.setdefault(key, _empty_grid_cell())
        cell["weightedCount"] += GREENERY_WEIGHTS[greenery_type]
        cell["counts"][greenery_type] += 1
        current = cell["representative"]
        if current is None or (
            REPRESENTATIVE_PRIORITY[greenery_type] > REPRESENTATIVE_PRIORITY[current["type"]]
        ):
            cell["representative"] = point

    for area in green_areas:
        coordinate = area_cell_center((int(area["x"]), int(area["y"])))
        key = _grid_key(coordinate)
        cell = grid.setdefault(key, _empty_grid_cell())
        weight = float(area["weight"])
        if weight >= cell["areaBonus"]:
            cell["areaBonus"] = weight
            cell["greenArea"] = area
    return grid


def _green_level(grid: dict[GridKey, dict[str, Any]], key: GridKey) -> float:
    weighted_score = 0.0
    for offset_x in range(-1, 2):
        for offset_y in range(-1, 2):
            cell = grid.get((key[0] + offset_x, key[1] + offset_y))
            if not cell:
                continue
            if offset_x == 0 and offset_y == 0:
                influence = 1.0
            elif offset_x == 0 or offset_y == 0:
                influence = 0.4
            else:
                influence = 0.2
            weighted_score += (cell["weightedCount"] + cell["areaBonus"]) * influence
    return 1.0 - math.exp(-weighted_score / GREEN_SATURATION_SCORE)


def _grid_distance(first: GridKey, second: GridKey) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1]) * CELL_SIZE_METERS


def _warsaw_grid_bounds() -> tuple[int, int, int, int]:
    west, south = _grid_key([WARSAW_GREEN_BOUNDS["west"], WARSAW_GREEN_BOUNDS["south"]])
    east, north = _grid_key([WARSAW_GREEN_BOUNDS["east"], WARSAW_GREEN_BOUNDS["north"]])
    return west, south, east, north


def _neighbors(key: GridKey) -> list[GridKey]:
    return [
        (key[0] + offset_x, key[1] + offset_y)
        for offset_x in range(-1, 2)
        for offset_y in range(-1, 2)
        if offset_x or offset_y
    ]


def _green_corridor_path(
    grid: dict[GridKey, dict[str, Any]], start: GridKey, destination: GridKey
) -> list[GridKey]:
    west, south, east, north = _warsaw_grid_bounds()
    direct_distance = max(_grid_distance(start, destination), CELL_SIZE_METERS)
    search_limit = min(
        direct_distance * SEARCH_DETOUR_RATIO,
        direct_distance + SEARCH_EXTRA_METERS,
    )
    frontier: list[tuple[float, GridKey]] = [(0.0, start)]
    came_from: dict[GridKey, GridKey] = {}
    cost_so_far = {start: 0.0}
    green_levels: dict[GridKey, float] = {}

    while frontier:
        _, current = heapq.heappop(frontier)
        if current == destination:
            break

        for neighbor in _neighbors(current):
            if not (west <= neighbor[0] <= east and south <= neighbor[1] <= north):
                continue
            if (
                _grid_distance(start, neighbor) + _grid_distance(neighbor, destination)
                > search_limit
            ):
                continue

            step_distance = _grid_distance(current, neighbor)
            level = green_levels.setdefault(neighbor, _green_level(grid, neighbor))
            step_cost = step_distance * (1.0 + BARREN_AREA_PENALTY * (1.0 - level))
            new_cost = cost_so_far[current] + step_cost
            if new_cost >= cost_so_far.get(neighbor, math.inf):
                continue
            cost_so_far[neighbor] = new_cost
            priority = new_cost + _grid_distance(neighbor, destination)
            heapq.heappush(frontier, (priority, neighbor))
            came_from[neighbor] = current

    if destination not in cost_so_far:
        return []

    path = [destination]
    while path[-1] != start:
        path.append(came_from[path[-1]])
    path.reverse()
    return path


def _nearby_counts(grid: dict[GridKey, dict[str, Any]], key: GridKey) -> dict[str, int]:
    counts = {"tree": 0, "shrub": 0, "forest": 0}
    for offset_x in range(-1, 2):
        for offset_y in range(-1, 2):
            cell = grid.get((key[0] + offset_x, key[1] + offset_y))
            if not cell:
                continue
            for greenery_type, count in cell["counts"].items():
                counts[greenery_type] += count
    return counts


def _corridor_waypoints(
    path: list[GridKey],
    grid: dict[GridKey, dict[str, Any]],
    *,
    max_waypoints: int,
) -> list[dict[str, Any]]:
    candidates = []
    for index, path_key in enumerate(path[2:-2], start=2):
        nearby_green_cells = [
            (path_key[0] + offset_x, path_key[1] + offset_y)
            for offset_x in range(-1, 2)
            for offset_y in range(-1, 2)
            if (path_key[0] + offset_x, path_key[1] + offset_y) in grid
        ]
        if nearby_green_cells:
            candidates.append(
                (
                    index,
                    max(
                        nearby_green_cells,
                        key=lambda key: (
                            _green_level(grid, key),
                            grid[key]["weightedCount"] + grid[key]["areaBonus"],
                        ),
                    ),
                )
            )
    if not candidates:
        return []

    path_distance = max((len(path) - 1) * CELL_SIZE_METERS, CELL_SIZE_METERS)
    waypoint_count = min(
        max_waypoints,
        max(1, math.ceil(path_distance / WAYPOINT_SPACING_METERS)),
        len(candidates),
    )
    bin_width = max(len(path) / waypoint_count, 1.0)
    bins: list[list[tuple[int, GridKey]]] = [[] for _ in range(waypoint_count)]
    for index, key in candidates:
        bin_index = min(waypoint_count - 1, int(index / bin_width))
        bins[bin_index].append((index, key))

    selected: list[tuple[int, GridKey]] = []
    for bin_candidates in bins:
        if not bin_candidates:
            continue
        selected.append(
            max(
                bin_candidates,
                key=lambda candidate: (
                    _green_level(grid, candidate[1]),
                    grid[candidate[1]]["weightedCount"] + grid[candidate[1]]["areaBonus"],
                ),
            )
        )
    selected.sort()

    waypoints = []
    seen_coordinates = set()
    for _, key in selected:
        cell = grid[key]
        point = cell["representative"]
        if point is None:
            longitude, latitude = _cell_center(key)
            point = {"lat": latitude, "lon": longitude}
        coordinate_key = (round(point["lat"], 6), round(point["lon"], 6))
        if coordinate_key in seen_coordinates:
            continue
        seen_coordinates.add(coordinate_key)
        counts = _nearby_counts(grid, key)
        waypoints.append(
            {
                "lat": point["lat"],
                "lon": point["lon"],
                "type": "through",
                "treeCount": counts["tree"],
                "shrubCount": counts["shrub"],
                "forestCount": counts["forest"],
                "greenLevel": round(_green_level(grid, key) * 100),
                "greenArea": cell["greenArea"]["category"] if cell["greenArea"] else None,
                "greenAreaName": cell["greenArea"]["name"] if cell["greenArea"] else None,
            }
        )
    return waypoints


def build_green_corridor_waypoints(
    baseline_route: Route,
    greenery: list[Point],
    green_areas: list[dict[str, Any]] | None = None,
    *,
    max_waypoints: int = MAX_GREEN_WAYPOINTS,
) -> list[dict[str, Any]]:
    """Build a citywide low-cost path through green cells, then return route anchors."""

    green_areas = green_areas or []
    if max_waypoints < 1 or (not greenery and not green_areas):
        return []
    coordinates = baseline_route["geometry"]["coordinates"]
    warsaw_coordinates = [coordinate for coordinate in coordinates if _inside_warsaw(coordinate)]
    if len(warsaw_coordinates) < 2:
        return []

    grid = _build_green_grid(greenery, green_areas)
    if not grid:
        return []
    start = _grid_key(warsaw_coordinates[0])
    destination = _grid_key(warsaw_coordinates[-1])
    if start == destination:
        return []
    path = _green_corridor_path(grid, start, destination)
    if not path:
        return []
    return _corridor_waypoints(path, grid, max_waypoints=max_waypoints)
