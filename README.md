# Weather Router

Weather Router is an operator-controlled director for scheduled weather-file
broadcasts. It is deliberately separate from QSSTV: weather acquisition,
schedules and operator decisions live here; modulation, radio control and the
final TX state transition remain in QSSTV.

The initial source catalogue supports the existing MetService downloader's
pressure charts, national rain radar, five-day rain forecast, Tasman infrared
satellite images, METAREA XIV chart, and high-seas text bulletins.

## Run it

QSSTV should already be running, normally with:

```sh
~/transmission/QSSTV/build/qsstv --headless
```

Then start the router:

```sh
cd ~/transmission/weather-router
python3 -m weather_router import
python3 -m weather_router serve
```

Open <http://127.0.0.1:8080/>. Transmission is inhibited by default and the
example daily schedule is disabled. Importing or fetching weather does not
transmit anything.

To make the UI available on one LAN address, for example, start it with
`WEATHER_ROUTER_HOST=172.16.10.200`. Use `0.0.0.0` only when it should listen
on every interface.
Authentication is not implemented in this first version, so do this only on a
trusted management LAN.

## Configuration

Environment variables:

| Variable | Default |
|---|---|
| `QSSTV_XMLRPC_URL` | `http://127.0.0.1:7362` |
| `WEATHER_ROUTER_HOST` | `127.0.0.1` |
| `WEATHER_ROUTER_PORT` | `8080` |
| `WEATHER_ROUTER_TIMEZONE` | `Pacific/Auckland` |
| `WEATHER_ROUTER_DB` | `./var/router.sqlite3` |
| `WEATHER_ROUTER_ASSETS` | `./var/assets` |
| `WEATHER_ROUTER_IMPORT` | `../metservice-maps` |
| `WEATHER_ROUTER_FETCH_SECONDS` | `1800` |

Schedules use local wall-clock time and explicit weekdays. A unique local slot
is persisted for each scheduled run, so a scheduler pass is idempotent. The
router does not replay missed slots after restart.
The running service refreshes MetService five seconds after startup and every
30 minutes thereafter by default.

## Safety model

- The router starts with global TX inhibit asserted.
- A received BSR is displayed but never approved automatically.
- QSSTV's BSR approval call only records approval; QSSTV's internal reconciler
  creates the FIX queue item later.
- Every broadcast is an immutable manifest of exact catalogue assets.
- Stable request IDs make QSSTV submission retries idempotent.
- Only QSSTV's own state owner can start modulation/PTT.

## Tests

```sh
cd ~/transmission/weather-router
python3 -m unittest discover -s tests -v
```
