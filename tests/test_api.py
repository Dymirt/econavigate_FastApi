from fastapi.testclient import TestClient

from econavigate.config import Settings
from econavigate.main import create_app


def make_app(tmp_path, **overrides):
    return create_app(
        Settings(
            _env_file=None,
            cache_dir=tmp_path / "cache",
            nominatim_min_interval_seconds=0,
            **overrides,
        )
    )


def test_health_reports_cache_and_missing_token(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["warsawTokenConfigured"] is False
    assert response.json()["cache"]["backend"] == "diskcache"


def test_air_requires_server_token_without_calling_upstream(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.get("/api/air")

    assert response.status_code == 503
    assert response.json() == {"error": "WARSAW_API_TOKEN is not configured on the server."}


def test_route_validation_uses_frontend_compatible_error(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post("/api/route", json={"from": "a", "to": "b", "mode": "walking"})

    assert response.status_code == 400
    assert response.json() == {"error": "Both places must contain at least three characters."}


def test_route_endpoint_preserves_response_contract(tmp_path):
    expected = {
        "from": {"label": "A"},
        "to": {"label": "B"},
        "mode": "walking",
        "districts": ["Śródmieście"],
        "selectedRouteId": "route-1",
        "routes": [],
        "greenery": [],
        "ecoCounts": {"tree": 0, "shrub": 0, "forest": 0},
        "warnings": [],
        "calculatedAt": "2026-07-14T00:00:00Z",
    }

    class FakeEcoService:
        async def build_green_route(self, request):
            assert request.from_query.lat == 52.2317
            assert request.from_query.lon == 21.006
            return expected

    with TestClient(make_app(tmp_path)) as client:
        client.app.state.eco = FakeEcoService()
        response = client.post(
            "/api/route",
            json={
                "from": {
                    "lat": 52.2317,
                    "lon": 21.006,
                    "label": "Your location",
                },
                "to": "Place B",
                "mode": "walking",
            },
        )

    assert response.status_code == 200
    assert response.json() == expected


def test_route_rejects_invalid_current_location_coordinates(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post(
            "/api/route",
            json={
                "from": {"lat": 120, "lon": 21.006},
                "to": "Place B",
                "mode": "walking",
            },
        )

    assert response.status_code == 400
