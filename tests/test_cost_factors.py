from econavigate.cost_factors import build_linear_cost_factors, route_retrace_ratio
from econavigate.green_areas import area_grid_key


def route(coordinates):
    return {
        "distance": 1_000,
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


def test_park_segments_receive_stronger_discount_than_tree_segments():
    coordinates = [[21.0, 52.2], [21.001, 52.2], [21.002, 52.2]]
    park_key = area_grid_key([21.0015, 52.2])
    green_areas = [
        {
            "x": park_key[0],
            "y": park_key[1],
            "category": "park",
            "weight": 22,
            "name": "Test Park",
        }
    ]
    greenery = [
        {"lon": 21.0003, "lat": 52.2, "type": "tree"},
    ]

    factors = build_linear_cost_factors(
        [route(coordinates)],
        greenery,
        green_areas,
        penalized_routes=[route(coordinates)],
    )

    factor_values = {feature["properties"]["factor"] for feature in factors}
    assert 0.12 in factor_values
    assert 0.34 in factor_values
    assert 3.5 in factor_values
    assert all(feature["geometry"]["type"] == "LineString" for feature in factors)


def test_retrace_ratio_detects_back_and_forth_on_same_road():
    clean = route([[21.0, 52.2], [21.001, 52.2], [21.002, 52.2]])
    retraced = route([[21.0, 52.2], [21.001, 52.2], [21.0002, 52.2], [21.002, 52.2]])

    assert route_retrace_ratio(clean) == 0
    assert route_retrace_ratio(retraced) > 0.2
