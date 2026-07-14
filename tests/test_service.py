from unittest.mock import AsyncMock

import pytest

from econavigate.cache import PersistentTTLCache
from econavigate.config import Settings
from econavigate.errors import ApiError
from econavigate.models import CurrentLocation
from econavigate.service import EcoService, route_district_probes


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


def test_route_district_probes_cover_the_route_inside_warsaw():
    coordinates = [
        [20.80, 52.20],
        [20.86, 52.20],
        [20.94, 52.21],
        [21.02, 52.22],
        [21.10, 52.23],
        [21.18, 52.24],
        [21.26, 52.25],
        [21.30, 52.25],
    ]
    routes = [
        {
            "distance": 20_000,
            "geometry": {"type": "LineString", "coordinates": coordinates},
        }
    ]

    assert route_district_probes(routes) == [
        [20.94, 52.21],
        [21.02, 52.22],
        [21.10, 52.23],
        [21.18, 52.24],
    ]
