import json
from unittest.mock import AsyncMock

import pytest

from econavigate.cache import PersistentTTLCache
from econavigate.config import Settings
from econavigate.errors import ApiError
from econavigate.models import CurrentLocation
from econavigate.service import EcoService


def make_service(tmp_path) -> EcoService:
    return EcoService(
        Settings(_env_file=None, cache_dir=tmp_path / "cache"),
        AsyncMock(),
        PersistentTTLCache(tmp_path / "cache", 10_000_000),
    )


@pytest.mark.asyncio
async def test_current_location_may_be_outside_warsaw(tmp_path):
    service = make_service(tmp_path)
    service._reverse_geocode = AsyncMock(
        return_value={"lat": 50.0614, "lon": 19.9366, "label": "Kraków", "district": None}
    )

    result = await service._resolve_origin(
        CurrentLocation(lat=50.0614, lon=19.9366, label="Your location")
    )

    assert result == {
        "lat": 50.0614,
        "lon": 19.9366,
        "label": "Your location",
        "district": None,
    }
    await service.cache.close()


@pytest.mark.asyncio
async def test_reverse_lookup_failure_does_not_reject_current_location(tmp_path):
    service = make_service(tmp_path)
    service._reverse_geocode = AsyncMock(side_effect=ApiError("lookup failed", 502))

    result = await service._resolve_origin(
        CurrentLocation(lat=52.1, lon=20.7, label="Your location")
    )

    assert result == {
        "lat": 52.1,
        "lon": 20.7,
        "label": "Your location",
        "district": None,
    }
    await service.cache.close()


@pytest.mark.asyncio
async def test_complete_greenery_resource_is_loaded_with_pagination(tmp_path):
    service = make_service(tmp_path)

    def record(identifier, longitude):
        return {
            "_id": identifier,
            "x_wgs84": longitude,
            "y_wgs84": 52.2,
            "gatunek": "oak",
            "stan_zdrowia": "good",
            "dzielnica": "Śródmieście",
            "adres": "Test street",
        }

    service.upstream.get_json.side_effect = [
        {"result": {"records": [record(1, 21.0), record(2, 21.01)], "total": 3}},
        {"result": {"records": [record(3, 21.02)], "total": 3}},
    ]

    points = await service._fetch_greenery_resource("tree")

    assert [point["id"] for point in points] == ["tree-1", "tree-2", "tree-3"]
    first_params = service.upstream.get_json.await_args_list[0].kwargs["params"]
    second_params = service.upstream.get_json.await_args_list[1].kwargs["params"]
    assert first_params["offset"] == "0"
    assert second_params["offset"] == "2"
    assert "filters" not in first_params
    assert first_params["sort"] == "_id asc"
    await service.cache.close()


@pytest.mark.asyncio
async def test_greenery_page_is_retried_after_a_transient_failure(tmp_path):
    service = make_service(tmp_path)
    service.upstream.get_json.side_effect = [
        ApiError("temporary failure", 502),
        {
            "result": {
                "records": [
                    {
                        "_id": 1,
                        "x_wgs84": 21.0,
                        "y_wgs84": 52.2,
                        "gatunek": "oak",
                    }
                ],
                "total": 1,
            }
        },
    ]

    points = await service._fetch_greenery_resource("tree")

    assert len(points) == 1
    assert service.upstream.get_json.await_count == 2
    await service.cache.close()


@pytest.mark.asyncio
async def test_green_area_polygons_are_loaded_from_overpass_and_cached(tmp_path):
    service = make_service(tmp_path)
    service.upstream.get_json.return_value = {
        "elements": [
            {
                "type": "way",
                "id": 9,
                "tags": {"leisure": "park", "name": "Test Park"},
                "geometry": [
                    {"lon": 20.998, "lat": 52.198},
                    {"lon": 21.002, "lat": 52.198},
                    {"lon": 21.002, "lat": 52.202},
                    {"lon": 20.998, "lat": 52.202},
                    {"lon": 20.998, "lat": 52.198},
                ],
            }
        ]
    }

    first = await service._fetch_green_areas()
    second = await service._fetch_green_areas()

    assert first["areaCount"] == 1
    assert first["cells"]
    assert second == first
    assert service.upstream.get_json.await_count == 1
    params = service.upstream.get_json.await_args.kwargs["params"]
    assert '"leisure"~"^(park|nature_reserve)$"' in params["data"]
    assert "out geom" in params["data"]
    await service.cache.close()


@pytest.mark.asyncio
async def test_green_corridor_waypoints_are_sent_as_ordered_through_locations(tmp_path):
    service = make_service(tmp_path)
    service.upstream.get_json.return_value = {
        "code": "Ok",
        "routes": [
            {
                "distance": 1_150,
                "duration": 900,
                "legs": [{"summary": "Generated"}],
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[21.0, 52.2], [21.005, 52.201], [21.01, 52.2]],
                },
            }
        ],
    }
    waypoints = [
        {"lat": 52.201, "lon": 21.004, "type": "through", "treeCount": 12},
        {"lat": 52.201, "lon": 21.007, "type": "through", "treeCount": 18},
    ]

    routes = await service._fetch_routes(
        {"lat": 52.2, "lon": 21.0},
        {"lat": 52.2, "lon": 21.01},
        "walking",
        waypoints=waypoints,
        alternates=0,
        route_kind="green-corridor",
    )

    request_json = json.loads(service.upstream.get_json.await_args.kwargs["params"]["json"])
    assert "alternates" not in request_json
    assert request_json["locations"] == [
        {"lat": 52.2, "lon": 21.0},
        {"lat": 52.201, "lon": 21.004, "type": "through"},
        {"lat": 52.201, "lon": 21.007, "type": "through"},
        {"lat": 52.2, "lon": 21.01},
    ]
    assert routes[0]["id"] == "green-corridor"
    assert routes[0]["routeKind"] == "green-corridor"
    assert routes[0]["greenWaypoints"] == waypoints
    await service.cache.close()
