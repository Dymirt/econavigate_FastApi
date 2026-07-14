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
            "distance": 1_020.0,
            "duration": 820.0,
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
    assert response["ecoCounts"]["tree"] == 19
    assert len(response["routes"]) == 2
    assert "_rankScore" not in response["routes"][0]
