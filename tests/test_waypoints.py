import pytest

from econavigate.green_areas import PARK_WEIGHT, area_grid_key
from econavigate.waypoints import build_green_corridor_waypoints


def _greenery(identifier: str, lon: float, lat: float, greenery_type: str = "tree"):
    return {
        "id": identifier,
        "type": greenery_type,
        "lon": lon,
        "lat": lat,
        "district": "Śródmieście",
        "name": "Greenery",
        "detail": "good",
    }


def test_citywide_optimizer_chooses_green_corridor_away_from_barren_direct_line():
    route = {
        "id": "route-1",
        "distance": 4_100.0,
        "geometry": {
            "type": "LineString",
            "coordinates": [[21.0, 52.2], [21.06, 52.2]],
        },
    }
    green_band = [
        _greenery(f"tree-{index}", 21.002 + index * 0.0015, 52.205) for index in range(38)
    ]

    waypoints = build_green_corridor_waypoints(route, green_band)

    assert len(waypoints) >= 3
    assert all(waypoint["lat"] == 52.205 for waypoint in waypoints)
    assert [waypoint["lon"] for waypoint in waypoints] == sorted(
        waypoint["lon"] for waypoint in waypoints
    )
    assert all(waypoint["treeCount"] > 0 for waypoint in waypoints)


@pytest.mark.parametrize(
    ("greenery_type", "count_field"),
    [("tree", "treeCount"), ("shrub", "shrubCount"), ("forest", "forestCount")],
)
def test_citywide_optimizer_uses_trees_shrubs_and_forests(greenery_type, count_field):
    route = {
        "id": "route-1",
        "distance": 2_000.0,
        "geometry": {
            "type": "LineString",
            "coordinates": [[21.0, 52.2], [21.03, 52.2]],
        },
    }
    greenery = [
        _greenery(f"{greenery_type}-{index}", 21.006 + index * 0.002, 52.201, greenery_type)
        for index in range(10)
    ]

    waypoints = build_green_corridor_waypoints(route, greenery)

    assert waypoints
    assert sum(waypoint[count_field] for waypoint in waypoints) > 0


def test_citywide_optimizer_returns_no_waypoints_without_greenery():
    route = {
        "id": "route-1",
        "distance": 1_000.0,
        "geometry": {
            "type": "LineString",
            "coordinates": [[21.0, 52.2], [21.01, 52.2]],
        },
    }

    assert build_green_corridor_waypoints(route, []) == []


def test_citywide_optimizer_can_build_a_corridor_from_park_area_cells():
    route = {
        "id": "route-1",
        "distance": 4_100.0,
        "geometry": {
            "type": "LineString",
            "coordinates": [[21.0, 52.2], [21.06, 52.2]],
        },
    }
    park_cells = {}
    for index in range(61):
        key = area_grid_key([21.0 + index * 0.001, 52.205])
        park_cells[key] = {
            "x": key[0],
            "y": key[1],
            "category": "park",
            "weight": PARK_WEIGHT,
            "name": "Linear Park",
        }

    waypoints = build_green_corridor_waypoints(route, [], list(park_cells.values()))

    assert len(waypoints) >= 3
    assert all(waypoint["greenArea"] == "park" for waypoint in waypoints)
    assert all(waypoint["greenAreaName"] == "Linear Park" for waypoint in waypoints)
    assert all(waypoint["lat"] > 52.203 for waypoint in waypoints)
