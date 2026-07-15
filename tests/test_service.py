from unittest.mock import AsyncMock

import pytest

from econavigate.cache import PersistentTTLCache
from econavigate.config import Settings
from econavigate.errors import ApiError
from econavigate.models import CurrentLocation, RouteRequest
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
    service.upstream.post_json.return_value = {
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

    request_json = service.upstream.post_json.await_args.kwargs["json"]
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


@pytest.mark.asyncio
async def test_green_cost_factors_are_sent_without_forcing_waypoints(tmp_path):
    service = make_service(tmp_path)
    service.upstream.post_json.return_value = {
        "code": "Ok",
        "routes": [
            {
                "distance": 1_050,
                "duration": 850,
                "legs": [{"summary": "Park path"}],
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[21.0, 52.2], [21.01, 52.2]],
                },
            }
        ],
    }
    waypoints = [{"lat": 52.201, "lon": 21.005, "greenArea": "park"}]
    factors = [
        {
            "type": "Feature",
            "properties": {"factor": 0.12},
            "geometry": {
                "type": "LineString",
                "coordinates": [[21.002, 52.2], [21.008, 52.2]],
            },
        }
    ]

    routes = await service._fetch_routes(
        {"lat": 52.2, "lon": 21.0},
        {"lat": 52.2, "lon": 21.01},
        "walking",
        alternates=0,
        route_kind="green-corridor",
        linear_cost_factors=factors,
        green_waypoints=waypoints,
    )

    request_json = service.upstream.post_json.await_args.kwargs["json"]
    assert request_json["locations"] == [
        {"lat": 52.2, "lon": 21.0},
        {"lat": 52.2, "lon": 21.01},
    ]
    assert request_json["linear_cost_factors"] == factors
    assert routes[0]["greenWaypoints"] == waypoints
    await service.cache.close()


@pytest.mark.asyncio
async def test_green_route_retries_and_penalizes_each_discovered_route(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    place_from = {"lat": 52.2, "lon": 21.0, "label": "A", "district": None}
    place_to = {"lat": 52.2, "lon": 21.01, "label": "B", "district": None}
    baseline = {
        "id": "route-1",
        "distance": 1_000,
        "duration": 700,
        "summary": "Baseline",
        "routeKind": "alternative",
        "greenWaypoints": [],
        "geometry": {"type": "LineString", "coordinates": [[21.0, 52.2], [21.01, 52.2]]},
    }
    probe = {**baseline, "id": "green-corridor", "routeKind": "green-corridor"}
    generated_count = 0

    async def fetch_routes(*_args, **kwargs):
        nonlocal generated_count
        if kwargs.get("waypoints"):
            return [probe]
        if kwargs.get("linear_cost_factors"):
            generated_count += 1
            latitude = 52.2 + generated_count * 0.0001
            return [
                {
                    **probe,
                    "distance": 1_000 + generated_count * 10,
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[21.0, 52.2], [21.005, latitude], [21.01, 52.2]],
                    },
                }
            ]
        return [baseline]

    service._resolve_origin = AsyncMock(return_value=place_from)
    service._geocode = AsyncMock(return_value=place_to)
    service._fetch_routes = AsyncMock(side_effect=fetch_routes)
    service._fetch_greenery = AsyncMock(return_value=([], []))
    service._fetch_green_areas = AsyncMock(return_value={"areaCount": 0, "cells": []})
    monkeypatch.setattr(
        "econavigate.service.build_green_corridor_waypoints",
        lambda *_args: [{"lat": 52.201, "lon": 21.005}],
    )
    penalized_route_counts = []

    def factors(*_args, penalized_routes):
        penalized_route_counts.append(len(penalized_routes))
        return [{"shape": "matched", "factor": 50.0}]

    monkeypatch.setattr("econavigate.service.build_linear_cost_factors", factors)
    monkeypatch.setattr("econavigate.service.route_retrace_ratio", lambda _route: 0.0)

    result = await service._build_green_route(
        RouteRequest.model_validate(
            {"from": {"lat": 52.2, "lon": 21.0}, "to": "Test destination", "mode": "walking"}
        )
    )

    assert generated_count == 3
    assert penalized_route_counts == [1, 2, 3]
    assert result["routingStrategy"] == "green-edge-costs"
    assert [route["id"] for route in result["routes"]] == [
        "green-corridor-1",
        "green-corridor-2",
        "green-corridor-3",
    ]
    await service.cache.close()
