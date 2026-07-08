#!/usr/bin/env python3
"""ESP32 telemetry simulator for the AquaEdge Edge API."""

from __future__ import annotations

import os
import random
import time
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()

EDGE_API_URL = os.getenv("EDGE_API_URL", "http://127.0.0.1:5000").rstrip("/")
EDGE_API_KEY = os.getenv("EDGE_API_KEY", "").strip()
DEVICE_ID = os.getenv("DEVICE_ID", "aquaedge-01").strip()
DEVICE_NAME = os.getenv("DEVICE_NAME", "Development Mock").strip()
FIRMWARE_VERSION = os.getenv("FIRMWARE_VERSION", "mock-1.0.0").strip()
TICK_INTERVAL_SEC = max(1.0, float(os.getenv("TICK_INTERVAL_SEC", "5")))

VALID_TARGETS = {"water_pump", "fertilizer_pump"}
VALID_STATES = {"ON", "OFF"}
VALID_WATER_LEVELS = {"EMPTY", "LOW", "SUFFICIENT", "FULL"}


def _initial_water_level() -> str:
    value = os.getenv("WATER_LEVEL", "SUFFICIENT").strip().upper()
    if value not in VALID_WATER_LEVELS:
        print(f"[Config] WATER_LEVEL={value!r} no es valido; se usara SUFFICIENT.")
        return "SUFFICIENT"
    return value


class SimulationState:
    def __init__(self) -> None:
        self.soil_moisture = float(os.getenv("SOIL_MOISTURE_START", "60"))
        self.soil_fertility = float(os.getenv("SOIL_FERTILITY_START", "3.5"))
        self.soil_temperature = 22.0
        self.air_temperature = 24.0
        self.air_humidity = 60.0
        self.water_level = _initial_water_level()
        self.water_pump = "OFF"
        self.fertilizer_pump = "OFF"
        self.water_pump_until: float | None = None
        self.fertilizer_pump_until: float | None = None
        self.tick_count = 0

    def expire_commands(self) -> None:
        now = time.monotonic()
        if self.water_pump_until is not None and now >= self.water_pump_until:
            self.water_pump = "OFF"
            self.water_pump_until = None
            print("[Command] water_pump apagada al terminar duration_sec.")
        if self.fertilizer_pump_until is not None and now >= self.fertilizer_pump_until:
            self.fertilizer_pump = "OFF"
            self.fertilizer_pump_until = None
            print("[Command] fertilizer_pump apagada al terminar duration_sec.")

    def update_sensors(self) -> None:
        self.soil_moisture = _clamp(
            self.soil_moisture - 0.3 + random.gauss(0, 0.1), 0.0, 100.0
        )
        self.soil_fertility = _clamp(
            self.soil_fertility - 0.05 + random.gauss(0, 0.02), 0.0, 10.0
        )
        self.soil_temperature = _clamp(
            self.soil_temperature + random.gauss(0, 0.3), 10.0, 40.0
        )
        self.air_temperature = _clamp(
            self.air_temperature + random.gauss(0, 0.3), 15.0, 35.0
        )
        self.air_humidity = _clamp(
            self.air_humidity + random.gauss(0, 1.0), 20.0, 95.0
        )

        if self.water_pump == "ON":
            self.soil_moisture = min(100.0, self.soil_moisture + 2.0)
        if self.fertilizer_pump == "ON":
            self.soil_fertility = min(10.0, self.soil_fertility + 0.5)

    def apply_command(self, command: Any) -> None:
        if not isinstance(command, dict):
            print(f"[Command] Ignorado: formato invalido ({command!r}).")
            return

        target = command.get("target")
        state = command.get("state")
        duration = command.get("duration_sec")

        if target not in VALID_TARGETS or state not in VALID_STATES:
            print(f"[Command] Ignorado: target/state invalidos ({target!r}, {state!r}).")
            return

        if state == "ON":
            if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                print(f"[Command] Ignorado: duration_sec invalido ({duration!r}).")
                return
            if not 1 <= duration <= 300:
                print(f"[Command] Ignorado: duration_sec fuera de rango ({duration!r}).")
                return
            deadline = time.monotonic() + float(duration)
        else:
            deadline = None

        setattr(self, target, state)
        setattr(self, f"{target}_until", deadline)
        suffix = f" por {duration:g}s" if state == "ON" else ""
        print(f"[Command] Aplicado: {target} {state}{suffix}.")


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def build_payload(state: SimulationState) -> dict[str, Any]:
    return {
        "meta": {
            "device_id": DEVICE_ID,
            "device_name": DEVICE_NAME,
            "firmware_version": FIRMWARE_VERSION,
            "tick_count": state.tick_count,
        },
        "sensors": {
            "soil_moisture": {"value": round(state.soil_moisture, 1), "unit": "%"},
            "soil_fertility": {
                "value": round(state.soil_fertility, 1),
                "unit": "index",
            },
            "soil_temperature": {
                "value": round(state.soil_temperature, 1),
                "unit": "C",
            },
            "air_temperature": {
                "value": round(state.air_temperature, 1),
                "unit": "C",
            },
            "air_humidity": {"value": round(state.air_humidity, 1), "unit": "%"},
            "water_level": {"status": state.water_level},
        },
        "actuators": {
            "water_pump": state.water_pump,
            "fertilizer_pump": state.fertilizer_pump,
        },
        "system_health": {"overall": "HEALTHY"},
    }


def _response_commands(response: requests.Response) -> list[Any]:
    if response.status_code == 204 or not response.content:
        return []
    try:
        body = response.json()
    except requests.exceptions.JSONDecodeError:
        print("[Edge] Respuesta exitosa sin JSON valido; no se aplicaron comandos.")
        return []

    if not isinstance(body, dict):
        print("[Edge] Respuesta JSON inesperada; no se aplicaron comandos.")
        return []
    commands = body.get("commands", [])
    if commands is None:
        return []
    if not isinstance(commands, list):
        print("[Edge] El campo commands no es una lista; fue ignorado.")
        return []
    return commands


def send_telemetry(session: requests.Session, payload: dict[str, Any]) -> list[Any]:
    headers = {"X-API-Key": EDGE_API_KEY} if EDGE_API_KEY else {}
    response = session.post(
        f"{EDGE_API_URL}/api/v1/telemetry",
        json=payload,
        headers=headers,
        timeout=10,
    )
    if response.status_code not in {200, 204}:
        raise requests.HTTPError(
            f"HTTP {response.status_code}: {response.text[:300]}",
            response=response,
        )
    return _response_commands(response)


def run_mock() -> None:
    if not EDGE_API_URL or not DEVICE_ID:
        raise SystemExit("ERROR: EDGE_API_URL y DEVICE_ID deben tener valor.")

    state = SimulationState()
    session = requests.Session()

    print("=" * 60)
    print("AquaEdge ESP32 Simulator")
    print(f"Device: {DEVICE_ID} | Edge: {EDGE_API_URL} | Tick: {TICK_INTERVAL_SEC:g}s")
    print("=" * 60)

    try:
        while True:
            tick_started = time.monotonic()
            state.tick_count += 1
            state.expire_commands()
            state.update_sensors()
            payload = build_payload(state)

            try:
                commands = send_telemetry(session, payload)
                print(
                    f"[Tick {state.tick_count:>5}] enviado "
                    f"M={state.soil_moisture:5.1f}% "
                    f"F={state.soil_fertility:4.1f} "
                    f"WP={state.water_pump} FP={state.fertilizer_pump} "
                    f"commands={len(commands)}"
                )
                for command in commands:
                    state.apply_command(command)
            except requests.RequestException as exc:
                print(f"[Edge] No se pudo enviar telemetria: {exc}. Reintentando.")

            elapsed = time.monotonic() - tick_started
            time.sleep(max(0.0, TICK_INTERVAL_SEC - elapsed))
    except KeyboardInterrupt:
        print("\nSimulador detenido.")
    finally:
        session.close()


if __name__ == "__main__":
    run_mock()
