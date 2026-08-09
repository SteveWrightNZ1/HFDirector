# Architecture and invariants

Weather Router is a director, not a modem. It does not manipulate sound devices,
rig control, PTT, DRM frames, or QSSTV internals.

```text
MetService ─┐
            ├→ source adapters → validated catalogue → run planner → QSSTV XML-RPC
ECMWF ──────┘                              ↑               ↓             ↓
                                      operator UI ← durable state ← TX history
                                           ↑
                                    pending BSR decisions
```

## Components

- `catalogue.py` validates, hashes, deduplicates and indexes weather products.
- `director.py` owns schedules, immutable run manifests and reconciliation.
- `qsstv.py` is the narrow XML-RPC modem adapter.
- `sources/metservice.py` adapts the existing downloader to the catalogue.
- `sources/ecmwf.py` downloads, attributes and prepares ECMWF OpenCharts.
- `sources/manager.py` presents one refresh interface to the scheduler and UI.
- `registry.py` defines stable logical products, provider capabilities and
  automatic fallback order.
- `web.py` exposes the operator console and read-only status endpoint.
- `db.py` defines the SQLite schema and settings.

## Safety invariants

1. Incoming RF cannot directly cause PTT.
2. Router XML-RPC calls create or approve durable intent; QSSTV owns the final
   transition into TX.
3. BSR automatic policy is inactive whenever router TX inhibit is asserted.
4. BSR approval and FIX queue creation are separate QSSTV state transitions.
5. QSSTV restart never resurrects an unfinished FIX without fresh approval.
6. Scheduled slots and QSSTV file requests are idempotent.
7. Missing or changed source files fail explicitly rather than transmitting a
   different payload from the manifest.
8. A modem communication failure never becomes a reported successful TX.
9. Forecast precipitation is never represented as observed rain radar.
10. A run records the exact provider asset selected; later source refreshes do
    not alter an existing manifest.

## Durable state

The router records settings, catalogue assets, schedules, broadcast runs,
broadcast items and BSR decisions. QSSTV separately records its TX queue, BSRs,
events and transition histories. Correlation uses `qsstv_queue_id`; stable
`weather-run-<run>-<position>` request IDs make retry safe.

This division allows other modem adapters to be added later without putting
weather acquisition or station policy inside QSSTV.
