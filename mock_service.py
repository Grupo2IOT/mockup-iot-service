#!/usr/bin/env python3
"""
AquaEdge IoT Mock Service

Simulates one AquaEdge IoT device and writes directly to Supabase PostgreSQL.

Usage:
    python -m venv .venv
    .venv\\Scripts\\activate
    pip install -r requirements.txt
    python mock_service.py
"""

from __future__ import annotations

import os
import random
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor
from psycopg2.sql import SQL, Identifier, Placeholder


load_dotenv(Path(__file__).with_name(".env"))

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DEVICE_CODE = os.environ.get("DEVICE_CODE", "aquaedge-01").strip()
TICK_INTERVAL = int(os.environ.get("TICK_INTERVAL_SEC", "5"))
VALID_WATER_LEVELS = {"EMPTY", "LOW", "MEDIUM", "FULL"}
VALID_SYSTEM_HEALTH_VALUES = {"OK", "WARNING", "ERROR", "CRITICAL"}
VALID_COMMAND_STATUSES = {"pending", "delivered", "executed", "rejected"}


def normalize_uuid(value: Any, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} debe ser un UUID valido, recibido: {value!r}') from exc


def normalize_water_level(value: str | None) -> str:
    normalized = (value or "FULL").strip().upper()

    if normalized not in VALID_WATER_LEVELS:
        print(
            f"[WARN] WATER_LEVEL={value!r} no es valido para el enum WaterLevel. "
            "Usando FULL."
        )
        return "FULL"

    return normalized


def normalize_system_health(value: str | None) -> str:
    normalized = (value or "OK").strip().upper()

    if normalized not in VALID_SYSTEM_HEALTH_VALUES:
        print(
            f"[WARN] systemHealth={value!r} no es valido para el enum SystemHealth. "
            "Usando OK."
        )
        return "OK"

    return normalized


@dataclass
class SimulationState:
    soil_moisture: float = float(os.environ.get("SOIL_MOISTURE_START", "60.0"))
    soil_fertility: float = float(os.environ.get("SOIL_FERTILITY_START", "3.5"))
    soil_temperature: float = 22.0
    air_temperature: float = 24.0
    air_humidity: float = 60.0
    water_level: str = normalize_water_level(os.environ.get("WATER_LEVEL", "FULL"))
    water_pump_state: str = "OFF"
    fertilizer_pump_state: str = "OFF"
    system_health: str = "OK"
    water_pump_timer: int = 0
    fertilizer_pump_timer: int = 0
    tick_count: int = 0


def connect():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL no esta definido en .env")
        raise SystemExit(1)

    try:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    except Exception as exc:
        print(f"ERROR: no se pudo conectar a PostgreSQL con DATABASE_URL: {exc}")
        raise SystemExit(1) from exc


def get_table_columns(conn, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table_name,),
        )
        return {row[0] for row in cur.fetchall()}


def get_required_tables_metadata(conn) -> dict[str, set[str]]:
    metadata = {
        "Device": get_table_columns(conn, "Device"),
        "Telemetry": get_table_columns(conn, "Telemetry"),
        "PendingCommand": get_table_columns(conn, "PendingCommand"),
    }

    missing = [name for name, columns in metadata.items() if not columns]
    if missing:
        print(f"ERROR: faltan tablas requeridas en PostgreSQL: {', '.join(missing)}")
        raise SystemExit(1)

    required_telemetry_columns = {"id", "deviceId"}
    missing_telemetry_columns = required_telemetry_columns - metadata["Telemetry"]
    if missing_telemetry_columns:
        print(
            'ERROR: faltan columnas requeridas en "Telemetry": '
            f"{', '.join(sorted(missing_telemetry_columns))}"
        )
        raise SystemExit(1)

    return metadata


def get_device(conn) -> dict[str, Any]:
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                'SELECT "id", "deviceCode" FROM "Device" WHERE "deviceCode" = %s LIMIT 1',
                (DEVICE_CODE,),
            )
            device = cur.fetchone()
    except Exception as exc:
        print(f'ERROR: no se pudo buscar el dispositivo en "Device": {exc}')
        raise SystemExit(1) from exc

    if not device:
        print(
            f'ERROR: no existe un dispositivo en "Device" con '
            f'"deviceCode" = {DEVICE_CODE!r}. Crea el dispositivo desde el backend antes de iniciar el mock.'
        )
        raise SystemExit(1)

    return dict(device)


def update_device_online(conn, device_id: str, device_columns: set[str]) -> None:
    values: dict[str, Any] = {}
    if "status" in device_columns:
        values["status"] = "ONLINE"
    if "lastSeenAt" in device_columns:
        values["lastSeenAt"] = SQL("NOW()")
    if "updatedAt" in device_columns:
        values["updatedAt"] = SQL("NOW()")

    if not values:
        return

    assignments = []
    params = []
    for column, value in values.items():
        if isinstance(value, SQL):
            assignments.append(SQL("{} = {}").format(Identifier(column), value))
        else:
            assignments.append(SQL("{} = {}").format(Identifier(column), Placeholder()))
            params.append(value)

    params.append(device_id)
    query = SQL('UPDATE "Device" SET {} WHERE "id" = {}').format(
        SQL(", ").join(assignments),
        Placeholder(),
    )

    with conn.cursor() as cur:
        cur.execute(query, params)


def update_sensors(state: SimulationState) -> None:
    state.soil_moisture = max(0.0, min(100.0, state.soil_moisture - 0.3 + random.gauss(0, 0.1)))
    state.soil_fertility = max(0.0, min(10.0, state.soil_fertility - 0.05 + random.gauss(0, 0.02)))
    state.soil_temperature = max(10.0, min(40.0, state.soil_temperature + random.gauss(0, 0.3)))
    state.air_temperature = max(15.0, min(35.0, state.air_temperature + random.gauss(0, 0.3)))
    state.air_humidity = max(20.0, min(95.0, state.air_humidity + random.gauss(0, 1.0)))

    if state.water_pump_state == "ON":
        state.soil_moisture = min(100.0, state.soil_moisture + 2.0)
    if state.fertilizer_pump_state == "ON":
        state.soil_fertility = min(10.0, state.soil_fertility + 0.5)

    state.system_health = "WARNING" if state.water_level == "EMPTY" else "OK"


def expire_pumps(state: SimulationState) -> None:
    if state.water_pump_timer > 0:
        state.water_pump_timer = max(0, state.water_pump_timer - TICK_INTERVAL)
        if state.water_pump_timer == 0:
            state.water_pump_state = "OFF"

    if state.fertilizer_pump_timer > 0:
        state.fertilizer_pump_timer = max(0, state.fertilizer_pump_timer - TICK_INTERVAL)
        if state.fertilizer_pump_timer == 0:
            state.fertilizer_pump_state = "OFF"


def build_telemetry(state: SimulationState, device_id: str, telemetry_columns: set[str]) -> dict[str, Any]:
    water_pump_on = state.water_pump_state == "ON"
    fertilizer_pump_on = state.fertilizer_pump_state == "ON"
    water_level = normalize_water_level(state.water_level)
    system_health = normalize_system_health(state.system_health)
    device_uuid = normalize_uuid(device_id, "deviceId")
    state.water_level = water_level
    state.system_health = system_health

    payload = {
        "id": str(uuid.uuid4()),
        "deviceId": device_uuid,
        "soilMoisture": round(state.soil_moisture, 2),
        "soilFertility": round(state.soil_fertility, 2),
        "soilTemperature": round(state.soil_temperature, 2),
        "airTemperature": round(state.air_temperature, 2),
        "airHumidity": round(state.air_humidity, 2),
        "waterLevel": water_level,
        "waterPumpState": state.water_pump_state,
        "fertilizerPumpState": state.fertilizer_pump_state,
        "systemHealth": system_health,
        "tickCount": state.tick_count,
        "createdAt": SQL("NOW()"),
        "updatedAt": SQL("NOW()"),
    }

    aliases = {
        "soil_moisture": round(state.soil_moisture, 2),
        "soil_fertility": round(state.soil_fertility, 2),
        "soil_temperature": round(state.soil_temperature, 2),
        "air_temperature": round(state.air_temperature, 2),
        "air_humidity": round(state.air_humidity, 2),
        "water_level": water_level,
        "waterPump": state.water_pump_state,
        "fertilizerPump": state.fertilizer_pump_state,
        "waterPumpOn": water_pump_on,
        "fertilizerPumpOn": fertilizer_pump_on,
        "pumpWater": water_pump_on,
        "pumpFertilizer": fertilizer_pump_on,
    }
    payload.update({key: value for key, value in aliases.items() if key in telemetry_columns})

    return {key: value for key, value in payload.items() if key in telemetry_columns}


def insert_telemetry(conn, telemetry: dict[str, Any]) -> None:
    if "id" not in telemetry:
        raise RuntimeError('La tabla "Telemetry" no tiene la columna requerida "id".')
    if "deviceId" not in telemetry:
        raise RuntimeError('La tabla "Telemetry" no tiene la columna requerida "deviceId".')

    columns = list(telemetry.keys())
    values = []
    placeholders = []

    for column in columns:
        value = telemetry[column]
        if isinstance(value, SQL):
            placeholders.append(value)
        else:
            placeholders.append(Placeholder())
            values.append(value)

    query = SQL('INSERT INTO "Telemetry" ({}) VALUES ({})').format(
        SQL(", ").join(Identifier(column) for column in columns),
        SQL(", ").join(placeholders),
    )

    with conn.cursor() as cur:
        cur.execute(query, values)


def fetch_pending_commands(conn, device_id: str) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT *
            FROM "PendingCommand"
            WHERE "deviceId" = %s AND "status" = %s
            ORDER BY "createdAt" ASC
            """,
            (device_id, "pending"),
        )
        return [dict(row) for row in cur.fetchall()]


def update_command(conn, command_id: str, status: str, command_columns: set[str]) -> None:
    if status not in VALID_COMMAND_STATUSES:
        raise ValueError(f"CommandStatus invalido: {status!r}")

    updates: dict[str, Any] = {"status": status}
    if status == "delivered" and "deliveredAt" in command_columns:
        updates["deliveredAt"] = SQL("NOW()")
    if status == "executed" and "executedAt" in command_columns:
        updates["executedAt"] = SQL("NOW()")
    if "updatedAt" in command_columns:
        updates["updatedAt"] = SQL("NOW()")

    assignments = []
    params = []
    for column, value in updates.items():
        if isinstance(value, SQL):
            assignments.append(SQL("{} = {}").format(Identifier(column), value))
        else:
            assignments.append(SQL("{} = {}").format(Identifier(column), Placeholder()))
            params.append(value)

    params.append(command_id)
    query = SQL('UPDATE "PendingCommand" SET {} WHERE "id" = {}').format(
        SQL(", ").join(assignments),
        Placeholder(),
    )

    with conn.cursor() as cur:
        cur.execute(query, params)


def normalize_command(command: dict[str, Any]) -> tuple[str | None, str | None, int]:
    payload = command.get("payload") or {}
    if isinstance(payload, str):
        payload = {"raw": payload}
    if not isinstance(payload, dict):
        payload = {}

    target = (
        command.get("target")
        or command.get("command")
        or command.get("type")
        or payload.get("target")
        or payload.get("command")
        or payload.get("type")
    )
    desired_state = (
        command.get("state")
        or command.get("desiredState")
        or payload.get("state")
        or payload.get("desiredState")
        or payload.get("value")
    )
    duration = command.get("durationSec") or payload.get("durationSec") or payload.get("duration") or 10

    target_map = {
        "water_pump": "water_pump",
        "WATER_PUMP": "water_pump",
        "IRRIGATION": "water_pump",
        "fertilizer_pump": "fertilizer_pump",
        "FERTILIZER_PUMP": "fertilizer_pump",
        "FERTILIZATION": "fertilizer_pump",
    }
    state_map = {
        True: "ON",
        False: "OFF",
        "on": "ON",
        "off": "OFF",
        "ON": "ON",
        "OFF": "OFF",
        "true": "ON",
        "false": "OFF",
    }

    return target_map.get(target, target), state_map.get(desired_state, desired_state), int(duration)


def apply_command(state: SimulationState, command: dict[str, Any]) -> None:
    target, desired_state, duration = normalize_command(command)

    if target not in {"water_pump", "fertilizer_pump"}:
        print(f'[Command] Ignorado id={command.get("id")} target desconocido: {target!r}')
        return
    if desired_state not in {"ON", "OFF"}:
        print(f'[Command] Ignorado id={command.get("id")} estado desconocido: {desired_state!r}')
        return

    if target == "water_pump":
        state.water_pump_state = desired_state
        state.water_pump_timer = duration if desired_state == "ON" else 0
    else:
        state.fertilizer_pump_state = desired_state
        state.fertilizer_pump_timer = duration if desired_state == "ON" else 0

    print(f'[Command] Ejecutado id={command.get("id")} {target}={desired_state} duration={duration}s')


def process_commands(conn, state: SimulationState, device_id: str, command_columns: set[str]) -> int:
    commands = fetch_pending_commands(conn, device_id)

    for command in commands:
        command_id = str(command["id"])
        update_command(conn, command_id, "delivered", command_columns)
        apply_command(state, command)
        update_command(conn, command_id, "executed", command_columns)

    return len(commands)


def run_mock() -> None:
    print("=" * 60)
    print("AquaEdge IoT Mock Service")
    print(f"Device code: {DEVICE_CODE} | Tick interval: {TICK_INTERVAL}s")
    print("=" * 60)

    conn = connect()
    state = SimulationState()

    try:
        metadata = get_required_tables_metadata(conn)
        device = get_device(conn)
        device_id = str(device["id"])
        conn.commit()

        print(f'OK: dispositivo encontrado en "Device": deviceCode={DEVICE_CODE} id={device_id}')
        print("OK: usando PostgreSQL directo con psycopg2. Iniciando simulacion...\n")

        while True:
            try:
                state.tick_count += 1
                expire_pumps(state)
                update_sensors(state)

                command_count = process_commands(conn, state, device_id, metadata["PendingCommand"])
                telemetry = build_telemetry(state, device_id, metadata["Telemetry"])
                insert_telemetry(conn, telemetry)
                update_device_online(conn, device_id, metadata["Device"])
                conn.commit()

                print(
                    f"[Tick {state.tick_count:>5}] "
                    f"SM={state.soil_moisture:5.1f}% "
                    f"SF={state.soil_fertility:4.1f} "
                    f"ST={state.soil_temperature:4.1f}C "
                    f"AT={state.air_temperature:4.1f}C "
                    f"AH={state.air_humidity:4.1f}% "
                    f"WL={state.water_level:10s} "
                    f"WP={state.water_pump_state:3s} "
                    f"FP={state.fertilizer_pump_state:3s} "
                    f"Health={state.system_health:7s} "
                    f"Cmds={command_count}"
                )
            except Exception as exc:
                conn.rollback()
                print(f"[ERROR] tick {state.tick_count}: {exc}")

            time.sleep(TICK_INTERVAL)
    except KeyboardInterrupt:
        print("\nShutdown requested. Exiting.")
    finally:
        conn.close()


if __name__ == "__main__":
    run_mock()
