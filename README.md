# HFDirector

HFDirector is an operator-controlled director for scheduled weather-file
broadcasts. It is deliberately separate from QSSTV: weather acquisition,
schedules and operator decisions live here; modulation, radio control and the
final TX state transition remain in QSSTV.

The source catalogue supports the existing MetService downloader plus
ready-rendered ECMWF OpenCharts. Schedules select logical weather products and
may pin each one to a provider or use an ordered automatic fallback.

## What it does

HFDirector runs continuously and:

1. Refreshes the configured MetService and ECMWF sources shortly after startup
   and at a configurable interval.
2. Validates downloaded images with Pillow, hashes them, copies them into a
   managed catalogue, and deduplicates repeated products.
3. Selects the latest asset for each product in an enabled schedule.
4. Freezes those exact assets into a durable broadcast-run manifest.
5. Submits each manifest item to QSSTV with a stable, idempotent request ID.
6. Reconciles the local run and item states with QSSTV's durable TX queue.
7. Displays received BSRs and supports manual or policy-controlled FIX approval.

The initial products are:

- Southwest Pacific surface-pressure analysis and H+30/H+48/H+72 forecasts
- National New Zealand rain-radar frames
- Five-day rain forecast frames
- Tasman Sea infrared satellite frames
- METAREA XIV responsibility chart
- Pacific, Subtropic, Forties and Southern high-seas bulletins

## Quick start

QSSTV should already be running, normally with:

```sh
~/transmission/QSSTV/build/qsstv --headless
```

Then start the router:

```sh
cd ~/transmission/HFDirector
python3 -m hf_director import
python3 -m hf_director serve
```

For the current workstation, `~/transmission/start` launches only HFDirector.
Its modem supervisor keeps FLDigi running as the idle RSID command
listener, using `~/transmission/.fldigi` and local XML-RPC port 7363. FLDigi is
forced into receive-only mode.

When a queued DRM file requires QSSTV, the supervisor stops FLDigi, starts
`QSSTV/build/qsstv --headless`, waits for its XML-RPC service, and then permits
the director to enqueue the file. Only one local modem process is active at a
time. After the QSSTV queue becomes idle, QSSTV remains active for a two-minute
BSR/FIX receive window before the supervisor restores FLDigi.

The supervisor exposes a generic registered-modem activation interface; QSSTV
and FLDigi are the first two adapters rather than permanent architectural roles.

For this station the router is started with:

```sh
cd ~/transmission/HFDirector
HFDIRECTOR_HOST=172.16.10.200 python3 -m hf_director serve
```

Open <http://172.16.10.200:8080/>. Transmission is inhibited by default and
the example daily schedule is disabled. Importing or fetching weather does not
transmit anything.

The first database initialization creates an administrator web login:

```text
Username: admin
Password: admin
```

Sign in, open `Users`, and replace the initial password before exposing the
console beyond the trusted management LAN. Passwords are stored as salted
Werkzeug hashes; the plaintext password is not retained.

To make the UI available on one LAN address, for example, start it with
`HFDIRECTOR_HOST=172.16.10.200`. Use `0.0.0.0` only when it should listen
on every interface.
Authentication is not implemented in this first version, so do this only on a
trusted management LAN.

## Operator workflow

### Weather catalogue

`Fetch MetService` runs the downloader immediately and catalogues its output.
`Import existing` catalogues the newest dated directory without making network
requests. The service also fetches automatically five seconds after startup and
every 30 minutes by default.

Each product card shows the most recently catalogued file. The catalogue keeps
content-addressed copies under `var/assets`; repeated downloads of identical
files do not create duplicate assets.

The `Sources` page is the acquisition and provenance console. It shows provider
availability and licensing, the logical-product compatibility matrix, latest
assets per provider, original source links, timestamps, dimensions and hashes.
It can refresh a complete provider or one ECMWF chart. ECMWF images use the
Australasia projection and are resized/compressed for practical DRM delivery;
the original OpenCharts URL and CC BY 4.0 attribution remain attached to the
catalogue record. NOAA is shown as planned, but cannot yet be selected for a
successful run unless an asset has been supplied by a future adapter.

### Schedules and broadcasts

A schedule contains a local time, DRM profile, an ordered comma-separated
product list, and a source policy for each product. `Automatic` follows the
compatibility matrix's provider order; selecting MetService, ECMWF, or NOAA pins
that product to that source. `Build run now` freezes the selected exact asset.
When TX is inhibited, the run remains `ready` and nothing is submitted.

To transmit a prepared run:

1. Check QSSTV is online and idle.
2. Review the exact items on the run-detail page.
3. Select `Enable transmission` in the red TX box.
4. Open the ready run and select `Submit to QSSTV`.
5. Follow `queued`, `sending`, and `sent` states on the run page.

For unattended daily operation, save and enable the schedule and leave TX
enabled. Scheduled runs are idempotent per local-time slot. A missed slot is
skipped after restart rather than replayed late.

### TX inhibit

TX inhibit prevents the router from submitting weather runs and pauses automatic
BSR approval. It does not stop an item that QSSTV has already accepted, because
QSSTV remains the owner of radio and modem state. Use QSSTV's own abort/rig
controls for an already-active transmission.

### BSR and FIX

QSSTV validates received `bsr.bin` files and exposes them as `pending`. Incoming
RF never directly causes PTT. The `BSR / FIX` page permits manual approval or
rejection.

The TX panel also provides automatic policy:

| Policy | Behaviour |
|---|---|
| `off` | No automatic action; operator reviews every request |
| `on` | Approve every valid pending BSR while TX is enabled |
| `whitelist` | Approve only callsigns in the list |
| `blacklist` | Approve all callsigns except those in the list |

Callsign matching is case-insensitive. One callsign per line or comma-separated
entries are accepted. An empty or unknown callsign does not match a whitelist;
it does pass an empty blacklist. Automatic policy is always paused while TX is
inhibited.

Approval itself does not transmit. It changes the durable BSR state to
`approved`; QSSTV's internal reconciler subsequently creates the FIX queue item
and starts it only when QSSTV is safely idle. After a QSSTV restart, unfinished
approved/FIX work returns to pending and requires fresh approval.

## Configuration

Environment variables:

| Variable | Default |
|---|---|
| `QSSTV_XMLRPC_URL` | `http://127.0.0.1:7362` |
| `HFDIRECTOR_HOST` | `127.0.0.1` |
| `HFDIRECTOR_PORT` | `8080` |
| `HFDIRECTOR_TIMEZONE` | `Pacific/Auckland` |
| `HFDIRECTOR_DB` | `./var/router.sqlite3` |
| `HFDIRECTOR_ASSETS` | `./var/assets` |
| `HFDIRECTOR_IMPORT` | `../metservice-maps` |
| `HFDIRECTOR_FETCH_SECONDS` | `1800` |

Schedules use local wall-clock time and explicit weekdays. A unique local slot
is persisted for each scheduled run, so a scheduler pass is idempotent. The
router does not replay missed slots after restart.
The running service refreshes implemented sources five seconds after startup
and every 30 minutes thereafter by default. ECMWF requests are deliberately
paced to respect the OpenCharts service.

## Running as a user service

A sample unit is supplied in `config/hfdirector.service`:

```sh
mkdir -p ~/.config/systemd/user
install -m 0644 config/hfdirector.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hfdirector.service
systemctl --user status hfdirector.service
```

Follow logs with:

```sh
journalctl --user -u hfdirector.service -f
```

The sample binds specifically to `172.16.10.200`. Edit its environment lines
if the station address or QSSTV endpoint changes.

## Persistence and recovery

Operational state is stored in `var/router.sqlite3` using SQLite WAL mode.
Downloaded catalogue copies are under `var/assets`. These runtime files are
excluded from Git and should be included in station backups.

Runs and request IDs survive router restarts. Resubmitting the same item is safe
because QSSTV returns the existing queue record for a repeated request ID. If
QSSTV becomes unavailable during a transmission, the router preserves the last
known item state instead of claiming success. Once QSSTV returns, reconciliation
continues from its durable queue history.

## Status API

`GET /api/status` returns JSON containing router inhibit state, modem status and
recent runs. State-changing operations are intentionally performed through the
operator forms rather than this small read-only endpoint.

## Troubleshooting

- **QSSTV Offline:** verify `qsstv --headless` is running and port 7362 is
  listening with `ss -ltnp | grep 7362`.
- **Router unreachable:** verify it is listening on the station address with
  `ss -ltnp | grep 8080` and check the host firewall.
- **Run remains ready:** TX is inhibited, a required product is missing, or the
  operator has not submitted the manual run.
- **Run state is stale:** QSSTV was unavailable. Do not assume the transmission
  completed; restart QSSTV and inspect the run and QSSTV history.
- **No new weather:** run `python3 ../metservice_maps.py`, inspect its errors,
  then select `Import existing`.

## Safety model

- The router starts with global TX inhibit asserted.
- A received BSR is displayed and is not approved automatically by default.
- Automatic BSR/FIX policy can be `off`, `on`, `whitelist`, or `blacklist`.
  It is paused whenever global TX inhibit is asserted. Callsigns are compared
  case-insensitively after whitespace is removed.
- QSSTV's BSR approval call only records approval; QSSTV's internal reconciler
  creates the FIX queue item later.
- Every broadcast is an immutable manifest of exact catalogue assets.
- Stable request IDs make QSSTV submission retries idempotent.
- Only QSSTV's own state owner can start modulation/PTT.

## Tests

```sh
cd ~/transmission/HFDirector
python3 -m unittest discover -s tests -v
```
