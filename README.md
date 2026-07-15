# Eco Navigate FastAPI

Eco Navigate finds walking and cycling alternatives through Warsaw and recommends the
route with stronger exposure to trees, shrubs, and forests without accepting an
unreasonable detour. This repository contains the production backend for the
[Eco Navigate frontend](https://github.com/Dymirt/Warsaw_moss).

The service keeps the Warsaw API token on the server. It replaces the frontend
repository's Node/Vercel backend and preserves its existing API contract.

## What it does

- Geocodes Warsaw addresses with Nominatim.
- Requests pedestrian or bicycle alternatives from Valhalla.
- Loads current tree, shrub, and forest inventories from Warsaw Open Data.
- Paginates through every Warsaw tree, shrub, and forest record before optimization.
- Loads public-style park and green-area polygons from OpenStreetMap through Overpass.
- Rasterizes polygons into a compact 60-metre area grid and caches it for seven days.
- Builds a citywide 120-metre green-density grid from the complete point and area data.
- Uses A* with a strong empty-area penalty to find a continuous green corridor.
- Gives parks the strongest area weight so routable paths through parks are preferred.
- Sends ordered corridor anchors to Valhalla as `through` locations to generate a route.
- Scores point inventories using only records within 5 metres of the route line.
- Adds route distance through parks and other mapped green areas to the green score.
- Returns route-specific greenery points and counts for every alternative.
- Returns every qualifying record without thinning map points.
- Selects the best green route after applying a detour penalty.
- Rejects a generated green route when it exceeds a 35% or 5 km detour, whichever is lower.
- Optionally returns live Warsaw air-quality stations.

No routing or map key is required by the current implementation. Valhalla and
Nominatim are public community services, and the frontend uses OpenStreetMap tiles.
For a high-traffic production service, self-host these dependencies or use a provider
with an SLA and follow its usage policy.

## Performance and caching

The API is asynchronous and reuses a pooled HTTP client. CPU-heavy corridor search and
route scoring run outside the async event loop. Spatial grids make the citywide A*
search and exact 5-metre route filtering practical with the complete inventory.

Responses are cached in a persistent DiskCache database:

| Data | Default TTL | Reason |
| --- | ---: | --- |
| Tree, shrub, and forest inventories | 7 days | These records change slowly and are expensive to download. |
| Park and green-area polygons | 7 days | Overpass geometry changes slowly and is compacted into grid cells. |
| Geocoding and district lookups | 30 days | Addresses rarely change. |
| Complete route results | 10 minutes | Makes repeated searches immediate while allowing routing updates. |
| Air quality | 5 minutes | Keeps sensor readings reasonably fresh. |

The cache survives process restarts when `CACHE_DIR` is backed by a persistent Docker
volume. Concurrent requests for the same uncached item are coalesced per worker.
The production image runs one worker to keep the complete in-memory inventory within
the 1 GB Compose limit. Increase workers only after increasing memory and measuring
real route workloads.

## API

Interactive OpenAPI documentation is available at `/docs`.

### `POST /api/route`

```json
{
  "from": {
    "lat": 52.2317,
    "lon": 21.006,
    "label": "Your location"
  },
  "to": "Łazienki Królewskie",
  "mode": "walking"
}
```

`from` accepts the browser's current coordinates or a Warsaw place name for backwards
compatibility. Coordinate origins may be outside Warsaw when the routing provider has
coverage, while destination search remains focused on Warsaw. Green scores use Warsaw's
inventory and therefore only reflect the part of a route covered by that data. `mode`
accepts `walking` or `cycling`. The response contains resolved endpoints, route
alternatives, the selected route ID, green scores, all greenery points within 5 metres
and their counts, per-route `greenAreaCoverage`, complete citywide `inventoryCounts`,
warnings for partially unavailable inventories, and a calculation timestamp.
`routingStrategy` reports whether the citywide green corridor produced an accepted
route, and `greenWaypoints` contains its ordered intermediate anchors and park names.

### `GET /api/air`

Returns `{ "stations": [...], "fetchedAt": "..." }`. This endpoint requires
`WARSAW_API_TOKEN` on the server.

### `GET /api/health`

Returns service readiness, whether the Warsaw token is configured, and non-sensitive
cache statistics.

## Local development

Requires Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env.local
uvicorn econavigate.main:app --reload
```

Put the real token in `.env.local`; local environment files are ignored by Git. Then open
<http://127.0.0.1:8000/docs>.

Run checks with:

```bash
ruff check .
ruff format --check .
pytest
```

## Deploy on your server

1. Clone this repository and copy `.env.example` to `.env.local`.
2. Set `WARSAW_API_TOKEN` and the exact frontend origins in `CORS_ORIGINS`.
3. Start the container:

   ```bash
   docker compose up -d --build
   docker compose ps
   docker compose logs -f api
   ```

4. Put Cloudflare Tunnel, Caddy, Nginx, or another TLS reverse proxy in front of
   port 8000. The Compose service listens on all host interfaces so a connector on
   another LAN machine can reach it. Restrict TCP 8000 to the connector's source IP
   with the host or network firewall; do not expose it directly to the internet.
5. Verify `https://your-api-domain.example/api/health` and `/docs`.

The Compose file publishes port 8000 on the server's network interfaces, runs one
Uvicorn worker, restarts the service after failures, and stores cached data in the
`eco-cache` volume.

For example, a Cloudflare Tunnel connector on the same LAN can use
`http://SERVER_LAN_IP:8000` as its origin. If the connector runs on the API server
itself, change the port mapping in `compose.yaml` back to
`127.0.0.1:8000:8000` to keep the origin loopback-only.

## Connect the Vercel frontend

The browser must use HTTPS for the API when the frontend is served over HTTPS. There
are two clean options after this service has an API domain:

- Configure the frontend to use `https://your-api-domain.example` as its API base URL
  and include the Vercel domain in `CORS_ORIGINS`.
- Add a Vercel rewrite from `/api/:path*` to
  `https://your-api-domain.example/api/:path*`. The frontend can then keep its current
  same-origin requests and no Vercel Function is needed.

Do not add `WARSAW_API_TOKEN` to Vercel or any `VITE_` variable. It belongs only in
this backend's `.env.local` file.

## Configuration

All settings use environment variables. The most useful ones are listed in
[`.env.example`](.env.example). Cache TTL values are seconds. Upstream URLs, timeouts,
connection pool sizes, cache size, and the Nominatim interval are also configurable;
see `econavigate/config.py` for the complete list.

## Data and routing sources

- [Warsaw Open Data API](https://api.um.warszawa.pl/) — tree, shrub, forest, and air-quality data.
- [OpenStreetMap Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API) — park,
  nature reserve, recreation ground, village green, and urban-greenery polygons.
- [OpenStreetMap Nominatim](https://nominatim.org/) — address and district lookup.
- [Valhalla](https://valhalla.github.io/valhalla/) — walking and cycling route alternatives.
- [OpenStreetMap](https://www.openstreetmap.org/copyright) — map and routing source data.

The green score is a route-ranking heuristic, not an official environmental or
accessibility rating. Always respect closures and on-site signage.
