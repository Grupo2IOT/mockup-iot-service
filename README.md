# mockup-iot-service README - AquaEdge Development Mock

Standalone Python service that simulates the AquaEdge IoT device and writes telemetry directly to the Supabase PostgreSQL database created by Prisma.

## Why this exists

Your Flutter and React teammates need real data to build dashboards, charts, and command UIs. This mock replaces the physical ESP32 and the local edge-api gateway — no hardware, no LAN hotspot, no waiting.

## What it simulates

- **Sensor dynamics**: soil moisture slowly drops, pumps raise it back up, temperature drifts, WiFi RSSI jitters.
- **Commands**: reads `"PendingCommand"`, marks commands as `delivered`, applies them to the internal mock state, then marks them as `executed`.
- **Device presence**: updates `"Device"."status"` to `ONLINE` and `"lastSeenAt"` on every tick.

## Quick Start

```powershell
cd mockup-iot-service
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env with DATABASE_URL and DEVICE_CODE
python mock_service.py
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | - | Supabase PostgreSQL connection string |
| `DEVICE_CODE` | No | `aquaedge-01` | Must match `"Device"."deviceCode"` |
| `TICK_INTERVAL_SEC` | No | `5` | Seconds between telemetry inserts |
| `SOIL_MOISTURE_START` | No | `60.0` | Initial moisture % |
| `SOIL_FERTILITY_START` | No | `3.5` | Initial fertility mS/cm |
| `WATER_LEVEL` | No | `FULL` | Valid values: `EMPTY`, `LOW`, `MEDIUM`, `FULL` |

## Architecture

```
mock_service.py -> Supabase PostgreSQL -> AquaEdge backend/frontend
```

The script uses `psycopg2-binary` and SQL parametrizado. It does not use `supabase-py`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, or a service role key.

## Troubleshooting

**"DATABASE_URL no esta definido en .env"** - Copy `.env.example` to `.env` and set `DATABASE_URL`.

**"no existe un dispositivo en Device"** - Create the device from the backend first. The mock searches by `"Device"."deviceCode" = DEVICE_CODE`.

**"permission denied"** - The database user in `DATABASE_URL` needs access to read `"Device"`/`"PendingCommand"` and write `"Telemetry"`/`"Device"`/`"PendingCommand"`.

## Next Steps

1. Run `mock_service.py` to start the live data stream.
2. Send commands from the backend by creating rows in `"PendingCommand"` with `status = 'pending'`.
3. Watch new rows in `"Telemetry"` and the device presence fields in `"Device"`.
