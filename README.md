# iot-mock README — AquaEdge Development Mock

> Standalone Python service that simulates the ESP32 + Edge-API pipeline by generating fake telemetry and inserting it directly into your live Supabase project.

## Why this exists

Your Flutter and React teammates need real data to build dashboards, charts, and command UIs. This mock replaces the physical ESP32 and the local edge-api gateway — no hardware, no LAN hotspot, no waiting.

## What it simulates

- **Sensor dynamics**: soil moisture slowly drops, pumps raise it back up, temperature drifts, WiFi RSSI jitters.
- **Autopilot**: moisture < 30% → `needs_irrigation`; fertility < 2.5 → `needs_fertilization`.
- **Safety (Tier 1)**: water tank `EMPTY` → all pump-ON commands rejected.
- **Commands**: reads the Supabase `pending_commands` table, executes them, updates status to `delivered` → `executed`/`rejected`.
- **Interactive scenarios**: toggle sensor failures and empty tank at runtime to test every UI state.

## Quick Start

```bash
cd iot-mock
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Supabase URL + service_role key
python mock_service.py
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SUPABASE_URL` | Yes | — | Project URL |
| `SUPABASE_SERVICE_KEY` | Yes | — | Service role key (never expose to frontend) |
| `DEVICE_ID` | No | `aquaedge-01` | Must match the device your apps query |
| `TICK_INTERVAL_SEC` | No | `5` | Seconds between telemetry inserts |
| `SOIL_MOISTURE_START` | No | `60.0` | Initial moisture % |
| `SOIL_FERTILITY_START` | No | `3.5` | Initial fertility mS/cm |
| `WATER_LEVEL` | No | `SUFFICIENT` | Start with `EMPTY` to test safety |
| `SENSOR_FAILURES` | No | — | Comma-list of failed sensors at boot |

## Interactive Keys

While `mock_service.py` is running, press:

| Key | Action |
|-----|--------|
| **w** | Queue `water_pump ON 10s` |
| **f** | Queue `fertilizer_pump ON 10s` |
| **e** | Toggle water level `SUFFICIENT ↔ EMPTY` |
| **s** | Cycle sensor failure modes |
| **d** | Dump current simulation state (JSON) |
| **q** | Quit |

## Seeding Historical Data

Generate a few hours of past data so charts look populated on first app launch:

```bash
python seed_data.py --hours 4
```

## How your apps consume this

### Flutter
```dart
supabase
  .from('telemetry')
  .stream(primaryKey: ['id'])
  .eq('device_id', 'aquaedge-01')
  .order('created_at')
  .limit(1)
  .listen((data) => setState(() => latest = data.first));
```

### React
```javascript
const subscription = supabase
  .channel('telemetry_changes')
  .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'telemetry', filter: 'device_id=eq.aquaedge-01' },
    (payload) => setTelemetry(payload.new)
  )
  .subscribe();
```

## Architecture

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────────────┐
│  iot-mock   │────→│   Supabase      │←────│  Flutter / React    │
│  (Python)   │     │   PostgreSQL    │     │  (Realtime WebSocket) │
└─────────────┘     └─────────────────┘     └─────────────────────┘
```

No HTTP polling needed — Supabase Realtime pushes every new `telemetry` row to connected clients instantly.

## Troubleshooting

**"supabase-py is not installed"** → Run `pip install -r requirements.txt`

**"SUPABASE_URL and SUPABASE_SERVICE_KEY must be set"** → Copy `.env.example` to `.env` and fill in your credentials.

**"permission denied for table telemetry"** → The `service_role` key is required (not the `anon` key) because this script inserts directly. Keep the service key secret — never commit it.

## Next Steps

1. Run `mock_service.py` to start the live data stream.
2. In your Supabase Dashboard → Database → Replication, enable Realtime on the `telemetry` and `pending_commands` tables.
3. Point your Flutter / React apps at the same Supabase project and subscribe to changes.
4. Use the interactive keys to trigger scenarios (empty tank, sensor failures) and watch your UI respond.
