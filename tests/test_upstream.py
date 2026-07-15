import httpx
import pytest

from econavigate.upstream import UpstreamClient


@pytest.mark.asyncio
async def test_post_json_sends_large_payload_in_request_body():
    observed_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_request
        observed_request = request
        return httpx.Response(200, json={"code": "Ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await UpstreamClient(client).post_json(
            "https://routing.example/route",
            source="Route service",
            json={"linear_cost_factors": [{"shape": "x" * 100_000}]},
        )

    assert result == {"code": "Ok"}
    assert observed_request is not None
    assert observed_request.url.query == b""
    assert len(observed_request.content) > 100_000
    assert observed_request.headers["content-type"] == "application/json"
