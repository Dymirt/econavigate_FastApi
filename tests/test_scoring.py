from econavigate.green_areas import PARK_WEIGHT, area_grid_key
from econavigate.scoring import build_route_response


def test_route_with_more_nearby_trees_is_selected():
    routes = [
        {
            "id": "route-1",
            "distance": 1_000.0,
            "duration": 800.0,
            "summary": "Grey route",
            "geometry": {
                "type": "LineString",
                "coordinates": [[21.0, 52.2], [21.01, 52.2]],
            },
        },
        {
            "id": "route-2",
            "distance": 1_250.0,
            "duration": 1_000.0,
            "summary": "Green route",
            "geometry": {
                "type": "LineString",
                "coordinates": [[21.0, 52.201], [21.01, 52.201]],
            },
        },
    ]
    greenery = [
        {
            "id": f"tree-{index}",
            "type": "tree",
            "lon": 21.0005 + index * 0.0005,
            "lat": 52.201,
            "district": "Śródmieście",
            "name": "Tree",
            "detail": "good",
        }
        for index in range(19)
    ]

    response = build_route_response(
        routes=routes,
        greenery=greenery,
        from_place={"lat": 52.2, "lon": 21.0, "label": "A", "district": "Śródmieście"},
        to_place={"lat": 52.2, "lon": 21.01, "label": "B", "district": "Śródmieście"},
        mode="walking",
        districts=["Śródmieście"],
        warnings=[],
    )

    assert response["selectedRouteId"] == "route-2"
    assert response["routes"][1]["detourPercent"] == 25
    assert response["ecoCounts"]["tree"] == 19
    assert len(response["routes"]) == 2
    assert "_rankScore" not in response["routes"][0]
    assert response["routes"][0]["ecoCounts"]["tree"] == 0
    assert response["routes"][0]["greenery"] == []
    assert response["routes"][1]["ecoCounts"]["tree"] == 19
    assert len(response["routes"][1]["greenery"]) == 19


def test_returns_every_tree_within_five_metres_and_excludes_farther_trees():
    route = {
        "id": "route-1",
        "distance": 1_000.0,
        "duration": 800.0,
        "summary": "Test route",
        "geometry": {
            "type": "LineString",
            "coordinates": [[21.0, 52.2], [21.01, 52.2]],
        },
    }
    four_metres_in_degrees = 4 / 111_320
    six_metres_in_degrees = 6 / 111_320
    nearby_trees = [
        {
            "id": f"tree-near-{index}",
            "type": "tree",
            "lon": 21.0001 + index * (0.0098 / 599),
            "lat": 52.2 + four_metres_in_degrees,
            "district": "Śródmieście",
            "name": "Tree",
            "detail": "good",
        }
        for index in range(600)
    ]
    farther_tree = {
        "id": "tree-too-far",
        "type": "tree",
        "lon": 21.005,
        "lat": 52.2 + six_metres_in_degrees,
        "district": "Śródmieście",
        "name": "Tree",
        "detail": "good",
    }

    response = build_route_response(
        routes=[route],
        greenery=[*nearby_trees, farther_tree],
        from_place={"lat": 52.2, "lon": 21.0, "label": "A", "district": "Śródmieście"},
        to_place={"lat": 52.2, "lon": 21.01, "label": "B", "district": "Śródmieście"},
        mode="walking",
        districts=["Śródmieście"],
        warnings=[],
    )

    displayed_ids = {point["id"] for point in response["greenery"]}
    assert len(displayed_ids) == 600
    assert response["ecoCounts"]["tree"] == 600
    assert "tree-too-far" not in displayed_ids


def test_route_through_a_park_can_beat_a_direct_route_with_trees():
    routes = [
        {
            "id": "route-1",
            "distance": 1_000.0,
            "duration": 800.0,
            "summary": "Direct route",
            "geometry": {
                "type": "LineString",
                "coordinates": [[21.0, 52.2], [21.01, 52.2]],
            },
        },
        {
            "id": "route-2",
            "distance": 1_100.0,
            "duration": 900.0,
            "summary": "Park route",
            "geometry": {
                "type": "LineString",
                "coordinates": [[21.0, 52.201], [21.01, 52.201]],
            },
        },
    ]
    park_cells = {}
    for index in range(21):
        key = area_grid_key([21.0 + index * 0.0005, 52.201])
        park_cells[key] = {
            "x": key[0],
            "y": key[1],
            "category": "park",
            "weight": PARK_WEIGHT,
            "name": "Test Park",
        }
    direct_route_trees = [
        {
            "id": f"direct-tree-{index}",
            "type": "tree",
            "lon": 21.0005 + index * 0.0006,
            "lat": 52.2,
            "district": "Śródmieście",
            "name": "Tree",
            "detail": "good",
        }
        for index in range(15)
    ]

    response = build_route_response(
        routes=routes,
        greenery=direct_route_trees,
        green_areas=list(park_cells.values()),
        from_place={"lat": 52.2, "lon": 21.0, "label": "A", "district": None},
        to_place={"lat": 52.2, "lon": 21.01, "label": "B", "district": None},
        mode="walking",
        districts=[],
        warnings=[],
    )

    assert response["selectedRouteId"] == "route-2"
    assert response["routes"][0]["ecoCounts"]["tree"] == 15
    assert response["routes"][1]["greenScore"] > response["routes"][0]["greenScore"]
    assert response["routes"][1]["greenAreaCoverage"]["parkMeters"] > 500
    assert response["routes"][1]["greenAreaCoverage"]["parks"] == ["Test Park"]
