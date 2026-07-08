#!/usr/bin/env python3
"""
AquaEdge IoT Mock Service

Simulates the full ESP32 → Edge-API → Supabase pipeline for local
frontend / mobile development. Generates fake telemetry every 5 seconds,
inserts it directly into your live Supabase project, and listens for
commands from the Supabase `pending_commands` table.

Usage:
    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env   # edit with your Supabase credentials
    python mock_service.py

Interactive keys while running:
    w   — send water_pump ON 10 s
    f   — send fertilizer_pump ON 10 s
    e   — toggle water level  SUFFICIENT ↔ EMPTY
    s   — cycle sensor failure mode
    d   — dump current state
    q   — quit
"""

from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load environment
# ---------------------------------------------------------------------------
load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
DEVICE_ID = os.environ.get("DEVICE_ID", "aquaedge-01")
DEVICE_NAME = os.environ.get("DEVICE_NAME", "Development Mock")
FIRMWARE_VERSION = os.environ.get("FIRMWARE_VERSION", "mock-1.0.0")
TICK_INTERVAL = int(os.environ.get("TICK_INTERVAL_SEC", "5"))

# ---------------------------------------------------------------------------
# Simulation state
# ---------------------------------------------------------------------------

class SimulationState:
    """Holds all mutable state for one mock device."""

    def __init__(self):
        # Sensor values
        self.soil_moisture = float(os.environ.get("SOIL_MOISTURE_START", "60.0"))
        self.soil_fertility = float(os.environ.get("SOIL_FERTILITY_START", "3.5"))
        self.soil_temperature = 22.0
        self.air_temperature = 24.0
        self.air_humidity = 60.0
        self.water_level = os.environ.get("WATER_LEVEL", "SUFFICIENT")
        self.wifi_rssi = -60

        # Actuators
        self.water_pump = "OFF"
        self.fertilizer_pump = "OFF"

        # Override timers (seconds remaining)
        self.water_pump_timer = 0
        self.fertilizer_pump_timer = 0

        # Counters
        self.tick_count = 0

        # Failure simulation
        self.failed_sensors: set[str] = set()
        failure_env = os.environ.get("SENSOR_FAILURES", "").strip()
        if failure_env:
            self.failed_sensors = {s.strip() for s in failure_env.split(",") if s.strip()}

    def is_sensor_valid(self, name: str) -> bool:
        return name not in self.failed_sensors

    def toggle_failure(self) -> str:
        """Cycle through failure modes and return a human-readable description."""
        modes = [
            set(),
            {"soil_moisture"},
            {"soil_fertility"},
            {"soil_temperature"},
            {"air"},
            {"water_level"},
            {"soil_moisture", "soil_fertility"},
        ]
        try:
            idx = modes.index(self.failed_sensors)
        except ValueError:
            idx = -1
        self.failed_sensors = modes[(idx + 1) % len(modes)]
        if self.failed_sensors:
            return f"Failed sensors: {', '.join(sorted(self.failed_sensors))}"
        return "All sensors healthy"

    def toggle_water_level(self) -> str:
        self.water_level = "EMPTY" if self.water_level == "SUFFICIENT" else "SUFFICIENT"
        return f"Water level: {self.water_level}"


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def _get_supabase():
    """Lazy import so the script can start even if supabase is missing."""
    try:
        from supabase import create_client
    except ImportError as exc:
        print("ERROR: supabase-py is not installed. Run: pip install -r requirements.txt")
        raise SystemExit(1) from exc

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        raise SystemExit(1)

    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _insert_telemetry(supabase, payload: dict) -> None:
    """Insert one telemetry row into Supabase."""
    try:
        supabase.table("telemetry").insert(payload).execute()
    except Exception as exc:
        print(f"[Supabase] telemetry insert error: {exc}")


def _fetch_pending_commands(supabase, device_id: str) -> list[dict]:
    """Return pending commands for this device, ordered oldest first."""
    try:
        resp = (
            supabase.table("pending_commands")
            .select("*")
            .eq("device_id", device_id)
            .eq("status", "pending")
            .order("created_at")
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        print(f"[Supabase] command fetch error: {exc}")
        return []


def _update_command_status(supabase, cmd_id: str, status: str, reason: str | None = None) -> None:
    """Update a command row to delivered / executed / rejected."""
    try:
        data: dict = {"status": status}
        if status == "delivered":
            data["delivered_at"] = datetime.now(timezone.utc).isoformat()
        elif status in ("executed", "rejected"):
            data["executed_at"] = datetime.now(timezone.utc).isoformat()
            if reason:
                data["result_reason"] = reason
        supabase.table("pending_commands").update(data).eq("id", cmd_id).execute()
    except Exception as exc:
        print(f"[Supabase] command update error: {exc}")


def _upsert_device(supabase, device_id: str) -> None:
    """Ensure the devices table has a row for this mock device."""
    try:
        # Upsert is tricky in supabase-py; do an update-or-insert dance
        resp = supabase.table("devices").select("id").eq("device_id", device_id).execute()
        now = datetime.now(timezone.utc).isoformat()
        if resp.data:
            supabase.table("devices").update({"last_seen_at": now}).eq("device_id", device_id).execute()
        else:
            supabase.table("devices").insert({
                "device_id": device_id,
                "name": DEVICE_NAME,
                "firmware_version": FIRMWARE_VERSION,
                "last_seen_at": now,
            }).execute()
    except Exception as exc:
        print(f"[Supabase] device upsert error: {exc}")


# ---------------------------------------------------------------------------
# Simulation logic
# ---------------------------------------------------------------------------

def _build_telemetry_payload(state: SimulationState) -> dict:
    """Convert the current simulation state into the exact JSON schema the edge-api expects."""
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Autopilot diagnosis
    needs_irrigation = state.soil_moisture < 30.0
    needs_fertilization = state.soil_fertility < 2.5
    alert = None
    if state.water_level == "EMPTY":
        alert = "Water tank is empty"
    elif needs_irrigation and needs_fertilization:
        alert = "Both irrigation and fertilization required"
    elif needs_irrigation:
        alert = "Irrigation required"
    elif needs_fertilization:
        alert = "Fertilization required"

    # Health
    failed = sorted(state.failed_sensors)
    overall = "HEALTHY"
    if failed:
        overall = "DEGRADED" if len(failed) < 3 else "CRITICAL"

    return {
        "meta": {
            "device_id": DEVICE_ID,
            "firmware_version": FIRMWARE_VERSION,
            "tick_count": state.tick_count,
            "timestamp_utc": ts,
            "wifi_rssi_dbm": round(state.wifi_rssi),
        },
        "sensors": {
            "soil_moisture": {
                "value": round(state.soil_moisture, 1) if state.is_sensor_valid("soil_moisture") else None,
                "unit": "%",
                "raw_adc": int((state.soil_moisture / 100.0) * 4095) if state.is_sensor_valid("soil_moisture") else 0,
                "is_valid": state.is_sensor_valid("soil_moisture"),
            },
            "soil_fertility": {
                "value": round(state.soil_fertility, 1) if state.is_sensor_valid("soil_fertility") else None,
                "unit": "mS/cm",
                "raw_adc": int((state.soil_fertility / 5.0) * 4095) if state.is_sensor_valid("soil_fertility") else 0,
                "is_valid": state.is_sensor_valid("soil_fertility"),
            },
            "soil_temperature": {
                "value": round(state.soil_temperature, 1) if state.is_sensor_valid("soil_temperature") else None,
                "unit": "C",
                "is_valid": state.is_sensor_valid("soil_temperature"),
            },
            "air": {
                "temperature": round(state.air_temperature, 1) if state.is_sensor_valid("air") else None,
                "humidity": round(state.air_humidity, 1) if state.is_sensor_valid("air") else None,
                "is_valid": state.is_sensor_valid("air"),
            },
            "water_level": {
                "status": state.water_level if state.is_sensor_valid("water_level") else "EMPTY",
                "is_valid": state.is_sensor_valid("water_level"),
            },
        },
        "diagnosis": {
            "needs_irrigation": needs_irrigation,
            "needs_fertilization": needs_fertilization,
            "alert_message": alert,
        },
        "actuators": {
            "water_pump": state.water_pump,
            "fertilizer_pump": state.fertilizer_pump,
        },
        "system_health": {
            "overall": overall,
            "failed_sensors": failed,
            "pending_commands": [],  # populated after command processing
        },
    }


def _flatten_for_supabase(payload: dict) -> dict:
    """Flatten the nested JSON payload into the flat telemetry table columns."""
    meta = payload["meta"]
    sensors = payload["sensors"]
    diagnosis = payload["diagnosis"]
    actuators = payload["actuators"]
    health = payload["system_health"]

    def b(val) -> bool | None:
        return bool(val) if val is not None else None

    return {
        "device_id": meta["device_id"],
        "tick_count": meta["tick_count"],
        "timestamp_utc": meta["timestamp_utc"],
        "wifi_rssi_dbm": meta["wifi_rssi_dbm"],
        "firmware_version": meta["firmware_version"],

        "soil_moisture_value": sensors["soil_moisture"].get("value"),
        "soil_moisture_raw_adc": sensors["soil_moisture"].get("raw_adc"),
        "soil_moisture_is_valid": b(sensors["soil_moisture"].get("is_valid")),

        "soil_fertility_value": sensors["soil_fertility"].get("value"),
        "soil_fertility_raw_adc": sensors["soil_fertility"].get("raw_adc"),
        "soil_fertility_is_valid": b(sensors["soil_fertility"].get("is_valid")),

        "soil_temperature_value": sensors["soil_temperature"].get("value"),
        "soil_temperature_is_valid": b(sensors["soil_temperature"].get("is_valid")),

        "air_temperature": sensors["air"].get("temperature"),
        "air_humidity": sensors["air"].get("humidity"),
        "air_is_valid": b(sensors["air"].get("is_valid")),

        "water_level_status": sensors["water_level"].get("status"),
        "water_level_is_valid": b(sensors["water_level"].get("is_valid")),

        "needs_irrigation": b(diagnosis["needs_irrigation"]),
        "needs_fertilization": b(diagnosis["needs_fertilization"]),
        "alert_message": diagnosis["alert_message"],

        "water_pump_state": actuators["water_pump"],
        "fertilizer_pump_state": actuators["fertilizer_pump"],

        "system_health_overall": health["overall"],
        "failed_sensors": health["failed_sensors"],
        "pending_commands": health["pending_commands"],
    }


def _update_sensors(state: SimulationState) -> None:
    """Apply natural drift and pump effects to sensor state."""
    # Natural drift
    state.soil_moisture = max(0.0, min(100.0, state.soil_moisture - 0.3 + random.gauss(0, 0.1)))
    state.soil_fertility = max(0.0, state.soil_fertility - 0.05 + random.gauss(0, 0.02))
    state.soil_temperature = max(10.0, min(40.0, state.soil_temperature + random.gauss(0, 0.3)))
    state.air_temperature = max(15.0, min(35.0, state.air_temperature + random.gauss(0, 0.3)))
    state.air_humidity = max(20.0, min(95.0, state.air_humidity + random.gauss(0, 1.0)))
    state.wifi_rssi = max(-90, min(-30, state.wifi_rssi + int(random.gauss(0, 2))))

    # Pump effects
    if state.water_pump == "ON":
        state.soil_moisture = min(100.0, state.soil_moisture + 2.0)
    if state.fertilizer_pump == "ON":
        state.soil_fertility = min(10.0, state.soil_fertility + 0.5)


def _process_commands(state: SimulationState, supabase, pending: list[dict]) -> list[dict]:
    """Apply safety rules, execute commands, return command results for telemetry."""
    results: list[dict] = []

    for cmd in pending:
        cmd_id = cmd["id"]
        target = cmd["target"]
        desired_state = cmd["state"]
        duration = cmd.get("duration_sec", 10)

        _update_command_status(supabase, cmd_id, "delivered")

        # Tier 1 safety checks
        if desired_state == "ON":
            if state.water_level == "EMPTY":
                results.append({
                    "command": target,
                    "executed": False,
                    "reason": "SAFETY_VIOLATION: tank_empty",
                })
                _update_command_status(supabase, cmd_id, "rejected", "SAFETY_VIOLATION: tank_empty")
                continue
            if not state.is_sensor_valid("water_level"):
                results.append({
                    "command": target,
                    "executed": False,
                    "reason": "SAFETY_VIOLATION: water_sensor_invalid",
                })
                _update_command_status(supabase, cmd_id, "rejected", "SAFETY_VIOLATION: water_sensor_invalid")
                continue

        # Execute
        if target == "water_pump":
            state.water_pump = desired_state
            state.water_pump_timer = duration if desired_state == "ON" else 0
        elif target == "fertilizer_pump":
            state.fertilizer_pump = desired_state
            state.fertilizer_pump_timer = duration if desired_state == "ON" else 0

        results.append({
            "command": target,
            "executed": True,
            "reason": "override_active",
        })
        _update_command_status(supabase, cmd_id, "executed", "override_active")

    return results


def _expire_overrides(state: SimulationState) -> None:
    """Decrement timers and turn pumps OFF when their override duration expires."""
    if state.water_pump_timer > 0:
        state.water_pump_timer -= TICK_INTERVAL
        if state.water_pump_timer <= 0:
            state.water_pump = "OFF"
            state.water_pump_timer = 0
    if state.fertilizer_pump_timer > 0:
        state.fertilizer_pump_timer -= TICK_INTERVAL
        if state.fertilizer_pump_timer <= 0:
            state.fertilizer_pump = "OFF"
            state.fertilizer_pump_timer = 0


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_mock():
    print("=" * 60)
    print("AquaEdge IoT Mock Service")
    print(f"Device: {DEVICE_ID}  |  Tick interval: {TICK_INTERVAL}s")
    print("=" * 60)

    supabase = _get_supabase()
    state = SimulationState()

    # Ensure device row exists
    _upsert_device(supabase, DEVICE_ID)

    print("\nConnected to Supabase. Starting simulation loop...")
    print("Keys: w=water  f=fertilizer  e=empty-toggle  s=sensor-failure  d=dump  q=quit\n")

    try:
        while True:
            state.tick_count += 1

            # 1. Expire old overrides
            _expire_overrides(state)

            # 2. Update sensors
            _update_sensors(state)

            # 3. Fetch pending commands from Supabase
            pending = _fetch_pending_commands(supabase, DEVICE_ID)

            # 4. Build payload
            payload = _build_telemetry_payload(state)

            # 5. Process commands (safety + execution)
            if pending:
                command_results = _process_commands(state, supabase, pending)
                payload["system_health"]["pending_commands"] = command_results

            # 6. Flatten and insert
            flat = _flatten_for_supabase(payload)
            _insert_telemetry(supabase, flat)
            _upsert_device(supabase, DEVICE_ID)

            # 7. Log
            failed_info = f" failed={','.join(state.failed_sensors)}" if state.failed_sensors else ""
            print(
                f"[Tick {state.tick_count:>5}] "
                f"M={state.soil_moisture:5.1f}% "
                f"F={state.soil_fertility:4.1f} "
                f"WP={state.water_pump:3s} "
                f"FP={state.fertilizer_pump:3s} "
                f"WL={state.water_level:10s} "
                f"Cmds={len(pending)}"
                f"{failed_info}"
            )

            time.sleep(TICK_INTERVAL)

    except KeyboardInterrupt:
        print("\n\nShutdown requested. Exiting.")


# ---------------------------------------------------------------------------
# Interactive key handler
# ---------------------------------------------------------------------------

def _interactive(state: SimulationState, supabase):
    """Runs in a background thread reading single keystrokes."""
    import tty
    import termios
    import select

    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin)
        while True:
            if select.select([sys.stdin], [], [], 0.1)[0]:
                ch = sys.stdin.read(1)
                if ch == "q":
                    print("\n[Interactive] Quit requested.")
                    os._exit(0)
                elif ch == "w":
                    # Inject a water_pump ON command directly into Supabase
                    try:
                        supabase.table("pending_commands").insert({
                            "device_id": DEVICE_ID,
                            "target": "water_pump",
                            "state": "ON",
                            "duration_sec": 10,
                            "status": "pending",
                        }).execute()
                        print("\n[Interactive] Queued: water_pump ON 10s")
                    except Exception as exc:
                        print(f"\n[Interactive] Error: {exc}")
                elif ch == "f":
                    try:
                        supabase.table("pending_commands").insert({
                            "device_id": DEVICE_ID,
                            "target": "fertilizer_pump",
                            "state": "ON",
                            "duration_sec": 10,
                            "status": "pending",
                        }).execute()
                        print("\n[Interactive] Queued: fertilizer_pump ON 10s")
                    except Exception as exc:
                        print(f"\n[Interactive] Error: {exc}")
                elif ch == "e":
                    msg = state.toggle_water_level()
                    print(f"\n[Interactive] {msg}")
                elif ch == "s":
                    msg = state.toggle_failure()
                    print(f"\n[Interactive] {msg}")
                elif ch == "d":
                    print(f"\n[Interactive] State dump: {json.dumps({
                        'moisture': state.soil_moisture,
                        'fertility': state.soil_fertility,
                        'water_pump': state.water_pump,
                        'fertilizer_pump': state.fertilizer_pump,
                        'water_level': state.water_level,
                        'failed_sensors': sorted(state.failed_sensors),
                    }, indent=2)}")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


def main():
    supabase = _get_supabase()
    state = SimulationState()

    # Start interactive thread
    t = threading.Thread(target=_interactive, args=(state, supabase), daemon=True)
    t.start()

    # Run main loop in main thread
    run_mock()


if __name__ == "__main__":
    main()
