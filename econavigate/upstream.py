import logging
from typing import Any

import httpx

from .errors import ApiError

logger = logging.getLogger(__name__)


class UpstreamClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_json(
        self,
        url: str,
        *,
        source: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = await self._client.get(url, params=params, headers=headers)
        except (httpx.TimeoutException, httpx.RequestError) as error:
            logger.warning("%s connection failed: %s", source, type(error).__name__)
            raise ApiError(f"{source} could not be reached. Please try again.", 502) from error

        if response.is_error:
            logger.warning("%s returned HTTP %s", source, response.status_code)
            raise ApiError(f"{source} returned an error. Please try again.", 502)

        try:
            return response.json()
        except ValueError as error:
            logger.warning("%s returned invalid JSON", source)
            raise ApiError(f"{source} returned an unreadable response.", 502) from error

    async def post_json(
        self,
        url: str,
        *,
        source: str,
        json: Any,
        headers: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = await self._client.post(url, json=json, headers=headers)
        except (httpx.TimeoutException, httpx.RequestError) as error:
            logger.warning("%s connection failed: %s", source, type(error).__name__)
            raise ApiError(f"{source} could not be reached. Please try again.", 502) from error

        if response.is_error:
            logger.warning("%s returned HTTP %s", source, response.status_code)
            raise ApiError(f"{source} returned an error. Please try again.", 502)

        try:
            return response.json()
        except ValueError as error:
            logger.warning("%s returned invalid JSON", source)
            raise ApiError(f"{source} returned an unreadable response.", 502) from error
