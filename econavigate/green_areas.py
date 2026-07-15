from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

Coordinate = list[float]
GridKey = tuple[int, int]

AREA_CELL_SIZE_METERS = 60.0
PARK_WEIGHT = 22.0
WARSAW_BOUNDS = {
    "west": 20.8517,
    "south": 52.0978,
    "east": 21.2712,
    "north": 52.3681,
}
AREA_TYPES = {
    ("leisure", "park"): ("park", PARK_WEIGHT),
    ("leisure", "nature_reserve"): ("nature_reserve", 14.0),
    ("landuse", "village_green"): ("park", 17.0),
    ("landuse", "recreation_ground"): ("recreation_ground", 13.0),
    ("landuse", "greenery"): ("green_area", 10.0),
}
BLOCKED_ACCESS = {"no", "private"}


def to_meters(coordinate: Coordinate) -> tuple[float, float]:
    longitude, latitude = coordinate
    return longitude * 67_800, latitude * 111_320


def area_grid_key(coordinate: Coordinate) -> GridKey:
    x, y = to_meters(coordinate)
    return math.floor(x / AREA_CELL_SIZE_METERS), math.floor(y / AREA_CELL_SIZE_METERS)


def area_cell_center(key: GridKey) -> Coordinate:
    return [
        (key[0] + 0.5) * AREA_CELL_SIZE_METERS / 67_800,
        (key[1] + 0.5) * AREA_CELL_SIZE_METERS / 111_320,
    ]


def green_area_grid(cells: list[dict[str, Any]]) -> dict[GridKey, dict[str, Any]]:
    return {(int(cell["x"]), int(cell["y"])): cell for cell in cells}


def _coordinate_key(coordinate: Coordinate) -> tuple[float, float]:
    return round(coordinate[0], 7), round(coordinate[1], 7)


def _geometry_coordinates(geometry: Any) -> list[Coordinate]:
    if not isinstance(geometry, list):
        return []
    coordinates = []
    for point in geometry:
        if not isinstance(point, dict):
            continue
        try:
            coordinates.append([float(point["lon"]), float(point["lat"])])
        except (KeyError, TypeError, ValueError):
            continue
    return coordinates


def _stitch_rings(segments: list[list[Coordinate]]) -> list[list[Coordinate]]:
    remaining = [segment for segment in segments if len(segment) >= 2]
    rings: list[list[Coordinate]] = []
    while remaining:
        ring = remaining.pop()
        while _coordinate_key(ring[0]) != _coordinate_key(ring[-1]):
            end = _coordinate_key(ring[-1])
            match_index = None
            reverse = False
            for index, segment in enumerate(remaining):
                if _coordinate_key(segment[0]) == end:
                    match_index = index
                    break
                if _coordinate_key(segment[-1]) == end:
                    match_index = index
                    reverse = True
                    break
            if match_index is None:
                break
            segment = remaining.pop(match_index)
            if reverse:
                segment.reverse()
            ring.extend(segment[1:])
        if len(ring) >= 4 and _coordinate_key(ring[0]) == _coordinate_key(ring[-1]):
            rings.append(ring)
    return rings


def _rings_for_element(
    element: dict[str, Any],
) -> tuple[list[list[Coordinate]], list[list[Coordinate]]]:
    if element.get("type") == "way":
        geometry = _geometry_coordinates(element.get("geometry"))
        if len(geometry) >= 4 and _coordinate_key(geometry[0]) == _coordinate_key(geometry[-1]):
            return [geometry], []
        return [], []

    outer_segments = []
    inner_segments = []
    for member in element.get("members") or []:
        geometry = _geometry_coordinates(member.get("geometry"))
        if member.get("role") == "inner":
            inner_segments.append(geometry)
        elif member.get("role") in {"", "outer", None}:
            outer_segments.append(geometry)
    return _stitch_rings(outer_segments), _stitch_rings(inner_segments)


def _grid_bounds() -> tuple[int, int, int, int]:
    west, south = area_grid_key([WARSAW_BOUNDS["west"], WARSAW_BOUNDS["south"]])
    east, north = area_grid_key([WARSAW_BOUNDS["east"], WARSAW_BOUNDS["north"]])
    return west, south, east, north


def _rasterize_ring(ring: list[Coordinate]) -> set[GridKey]:
    projected = [
        (x / AREA_CELL_SIZE_METERS, y / AREA_CELL_SIZE_METERS)
        for x, y in (to_meters(coordinate) for coordinate in ring)
    ]
    west, south, east, north = _grid_bounds()
    minimum_y = max(south, math.ceil(min(point[1] for point in projected) - 0.5))
    maximum_y = min(north, math.floor(max(point[1] for point in projected) - 0.5))
    cells: set[GridKey] = set()

    for cell_y in range(minimum_y, maximum_y + 1):
        scan_y = cell_y + 0.5
        intersections = []
        for start, end in pairwise(projected):
            if (start[1] <= scan_y < end[1]) or (end[1] <= scan_y < start[1]):
                intersections.append(
                    start[0] + (scan_y - start[1]) * (end[0] - start[0]) / (end[1] - start[1])
                )
        intersections.sort()
        for index in range(0, len(intersections) - 1, 2):
            minimum_x = max(west, math.ceil(intersections[index] - 0.5))
            maximum_x = min(east, math.floor(intersections[index + 1] - 0.5))
            cells.update((cell_x, cell_y) for cell_x in range(minimum_x, maximum_x + 1))
    return cells


def _area_type(tags: dict[str, Any]) -> tuple[str, float] | None:
    for (key, value), area_type in AREA_TYPES.items():
        if tags.get(key) == value:
            return area_type
    return None


def normalize_green_areas(payload: Any) -> dict[str, Any]:
    """Turn Overpass polygon geometry into a compact, citywide green-area grid."""

    elements = payload.get("elements") if isinstance(payload, dict) else None
    if not isinstance(elements, list):
        raise ValueError("Overpass green-area data had an unexpected format")

    cells: dict[GridKey, dict[str, Any]] = {}
    area_count = 0
    for element in elements:
        tags = element.get("tags") or {}
        area_type = _area_type(tags)
        if area_type is None or str(tags.get("access", "")).casefold() in BLOCKED_ACCESS:
            continue
        outer_rings, inner_rings = _rings_for_element(element)
        if not outer_rings:
            continue
        area_cells: set[GridKey] = set()
        for ring in outer_rings:
            area_cells.update(_rasterize_ring(ring))
        for ring in inner_rings:
            area_cells.difference_update(_rasterize_ring(ring))
        if not area_cells:
            continue

        category, weight = area_type
        name = str(tags.get("name") or tags.get("name:pl") or "").strip() or None
        area_count += 1
        for key in area_cells:
            current = cells.get(key)
            if current and float(current["weight"]) > weight:
                continue
            cells[key] = {
                "x": key[0],
                "y": key[1],
                "category": category,
                "weight": weight,
                "name": name,
            }

    return {"areaCount": area_count, "cells": list(cells.values())}
