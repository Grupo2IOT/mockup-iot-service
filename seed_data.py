#!/usr/bin/env python3
"""
AquaEdge Historical Data Seeder

Generates N hours of fake telemetry and inserts it into Supabase so your
Flutter / React teammates have a populated chart from day one.

Usage:
    cd iot-mock
    source .venv/bin/activate
    cp .env.example .env   # fill with your Supabase credentials
    python seed_data.py --hours 4

The script respects the same .env variables as mock_service.py.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load same .env as the mock service
env_path = Path(__file__).with_name(".env")
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
DEVICE_ID = os.environ.get("DEVICE_ID", "aquaedge-01")
FIRMWARE_VERSION = os.environ.get("FIRMWARE_VERSION", "mock-1.0.0")


def _get_supabase():
    try:
        from supabase import create_client
    except ImportError as exc:
        print("ERROR: supabase-py not installed. Run: pip install -r requirements.txt")
        raise SystemExit(1) from exc

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        raise SystemExit(1)

    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def generate_history(hours: int) -> list[dict]:
    """Generate a list of flat telemetry rows going back `hours` from now."""
    rows: list[dict] = []

    # Simulation parameters
    moisture = 60.0
    fertility = 3.5
    soil_temp = 22.0
    air_temp = 24.0
    air_humidity = 60.0
    wifi_rssi = -60
    water_level = "SUFFICIENT"
    water_pump = "OFF"
    fertilizer_pump = "OFF"

    total_ticks = int(hours * 3600 / 5)  # one tick every 5 seconds
    now = datetime.now(timezone.utc)

    for i in range(total_ticks, 0, -1):
        ts = now - timedelta(seconds=i * 5)
        tick = total_ticks - i + 1

        # Natural drift (reverse time, but same logic)
        moisture = max(0.0, min(100.0, moisture - 0.3 + random.gauss(0, 0.1)))
        fertility = max(0.0, fertility - 0.05 + random.gauss(0, 0.02))
        soil_temp = max(10.0, min(40.0, soil_temp + random.gauss(0, 0.3)))
        air_temp = max(15.0, min(35.0, air_temp + random.gauss(0, 0.3)))
        air_humidity = max(20.0, min(95.0, air_humidity + random.gauss(0, 1.0)))
        wifi_rssi = max(-90, min(-30, wifi_rssi + int(random.gauss(0, 2))))

        # Occasional pump events
        if random.random() < 0.005:  # 0.5% chance per tick
            water_pump = "ON"
        if water_pump == "ON" and random.random() < 0.05:
            water_pump = "OFF"

        if random.random() < 0.003:
            fertilizer_pump = "ON"
        if fertilizer_pump == "ON" and random.random() < 0.05:
            fertilizer_pump = "OFF"

        # Pump effects
        if water_pump == "ON":
            moisture = min(100.0, moisture + 2.0)
        if fertilizer_pump == "ON":
            fertility = min(10.0, fertility + 0.5)

        needs_irrigation = moisture < 30.0
        needs_fertilization = fertility < 2.5
        alert = None
        if water_level == "EMPTY":
            alert = "Water tank is empty"
        elif needs_irrigation and needs_fertilization:
            alert = "Both irrigation and fertilization required"
        elif needs_irrigation:
            alert = "Irrigation required"
        elif needs_fertilization:
            alert = "Fertilization required"

        rows.append({
            "created_at": ts.isoformat(),
            "device_id": DEVICE_ID,
            "tick_count": tick,
            "timestamp_utc": ts.isoformat().replace("+00:00", "Z"),
            "wifi_rssi_dbm": round(wifi_rssi),
            "firmware_version": FIRMWARE_VERSION,

            "soil_moisture_value": round(moisture, 1),
            "soil_moisture_raw_adc": int((moisture / 100.0) * 4095),
            "soil_moisture_is_valid": True,

            "soil_fertility_value": round(fertility, 1),
            "soil_fertility_raw_adc": int((fertility / 5.0) * 4095),
            "soil_fertility_is_valid": True,

            "soil_temperature_value": round(soil_temp, 1),
            "soil_temperature_is_valid": True,

            "air_temperature": round(air_temp, 1),
            "air_humidity": round(air_humidity, 1),
            "air_is_valid": True,

            "water_level_status": water_level,
            "water_level_is_valid": True,

            "needs_irrigation": needs_irrigation,
            "needs_fertilization": needs_fertilization,
            "alert_message": alert,

            "water_pump_state": water_pump,
            "fertilizer_pump_state": fertilizer_pump,

            "system_health_overall": "HEALTHY",
            "failed_sensors": [],
            "pending_commands": [],
        })

    return rows


def batch_insert(supabase, rows: list[dict], batch_size: int = 500) -> None:
    """Insert rows in batches to avoid payload size limits."""
    total = len(rows)
    for start in range(0, total, batch_size):
        batch = rows[start : start + batch_size]
        try:
            supabase.table("telemetry").insert(batch).execute()
            print(f"Inserted {start + len(batch)} / {total} rows...")
        except Exception as exc:
            print(f"Batch insert failed at offset {start}: {exc}")
            raise SystemExit(1) from exc


def ensure_device(supabase) -> None:
    """Create the device row if it does not exist."""
    try:
        resp = supabase.table("devices").select("id").eq("device_id", DEVICE_ID).execute()
        if not resp.data:
            supabase.table("devices").insert({
                "device_id": DEVICE_ID,
                "name": "Development Mock (seeded)",
                "firmware_version": FIRMWARE_VERSION,
            }).execute()
            print(f"Created device row for {DEVICE_ID}")
    except Exception as exc:
        print(f"Device upsert warning: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Seed fake telemetry history into Supabase")
    parser.add_argument("--hours", type=int, default=4, help="Hours of history to generate (default: 4)")
    parser.add_argument("--batch", type=int, default=500, help="Rows per insert batch (default: 500)")
    args = parser.parse_args()

    print(f"Generating {args.hours} hours of fake telemetry ({args.hours * 720} rows)...")
    rows = generate_history(args.hours)

    print("Connecting to Supabase...")
    supabase = _get_supabase()

    ensure_device(supabase)

    print("Inserting rows...")
    batch_insert(supabase, rows, args.batch)

    print(f"Done. {len(rows)} rows inserted into telemetry table.")
    print(f"Query in Supabase SQL Editor:")
    print(f"  SELECT COUNT(*) FROM telemetry WHERE device_id = '{DEVICE_ID}';")


if __name__ == "__main__":
    main()
