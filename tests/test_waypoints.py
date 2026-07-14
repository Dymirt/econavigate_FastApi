from econavigate.waypoints import select_green_waypoints


def _tree(identifier: str, lon: float, lat: float = 52.2):
    return {
        "id": identifier,
        "type": "tree",
        "lon": lon,
        "lat": lat,
        "district": "Śródmieście",
        "name": "Tree",
        "detail": "good",
    }


def test_selects_ordered_waypoints_from_tree_clusters_near_route():
    route = {
        "id": "route-1",
        "distance": 5_400.0,
        "geometry": {
            "type": "LineString",
            "coordinates": [[21.0, 52.2], [21.08, 52.2]],
        },
    }
    first_cluster = [_tree(f"west-{index}", 21.015 + index * 0.00001) for index in range(8)]
    second_cluster = [_tree(f"east-{index}", 21.06 + index * 0.00001) for index in range(12)]
    far_away = [_tree(f"far-{index}", 21.04, 52.21) for index in range(30)]

    waypoints = select_green_waypoints(
        route,
        [*first_cluster, *second_cluster, *far_away],
    )

    assert len(waypoints) == 2
    assert waypoints[0]["lon"] < waypoints[1]["lon"]
    assert all(waypoint["type"] == "through" for waypoint in waypoints)
    assert {waypoint["treeCount"] for waypoint in waypoints} == {8, 12}


def test_returns_no_waypoints_without_trees_near_route():
    route = {
        "id": "route-1",
        "distance": 1_000.0,
        "geometry": {
            "type": "LineString",
            "coordinates": [[21.0, 52.2], [21.01, 52.2]],
        },
    }

    assert select_green_waypoints(route, [_tree("far", 21.005, 52.21)]) == []
