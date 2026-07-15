from econavigate.green_areas import area_grid_key, normalize_green_areas


def _geometry(coordinates):
    return [{"lon": longitude, "lat": latitude} for longitude, latitude in coordinates]


def test_overpass_polygons_are_rasterized_and_private_areas_are_ignored():
    outer = [
        [20.998, 52.198],
        [21.002, 52.198],
        [21.002, 52.202],
        [20.998, 52.202],
        [20.998, 52.198],
    ]
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 1,
                "tags": {"leisure": "park", "name": "Test Park"},
                "geometry": _geometry(outer),
            },
            {
                "type": "way",
                "id": 2,
                "tags": {"leisure": "park", "access": "private"},
                "geometry": _geometry(outer),
            },
        ]
    }

    result = normalize_green_areas(payload)

    cells = {(cell["x"], cell["y"]): cell for cell in result["cells"]}
    assert result["areaCount"] == 1
    assert area_grid_key([21.0, 52.2]) in cells
    assert cells[area_grid_key([21.0, 52.2])]["category"] == "park"
    assert cells[area_grid_key([21.0, 52.2])]["name"] == "Test Park"


def test_relation_segments_are_stitched_into_a_green_area():
    first_half = [[20.998, 52.198], [21.002, 52.198], [21.002, 52.202]]
    second_half = [[21.002, 52.202], [20.998, 52.202], [20.998, 52.198]]
    payload = {
        "elements": [
            {
                "type": "relation",
                "id": 3,
                "tags": {"landuse": "recreation_ground", "name": "Green Field"},
                "members": [
                    {"type": "way", "role": "outer", "geometry": _geometry(first_half)},
                    {"type": "way", "role": "outer", "geometry": _geometry(second_half)},
                ],
            }
        ]
    }

    result = normalize_green_areas(payload)

    assert result["areaCount"] == 1
    assert any(cell["category"] == "recreation_ground" for cell in result["cells"])
