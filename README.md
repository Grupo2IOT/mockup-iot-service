# AquaEdge ESP32 Simulator

Simulador local del dispositivo ESP32. Genera telemetria, la envia al Edge API
por HTTP y aplica los comandos devueltos por el mismo endpoint.

## Configuracion

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Variables disponibles:

| Variable | Valor predeterminado |
|---|---|
| `EDGE_API_URL` | `http://127.0.0.1:5000` |
| `EDGE_API_KEY` | Vacio; omite el header `X-API-Key` |
| `DEVICE_ID` | `aquaedge-01` |
| `DEVICE_NAME` | `Development Mock` |
| `FIRMWARE_VERSION` | `mock-1.0.0` |
| `TICK_INTERVAL_SEC` | `5` |
| `SOIL_MOISTURE_START` | `60` |
| `SOIL_FERTILITY_START` | `3.5` |
| `WATER_LEVEL` | `SUFFICIENT` |

## Ejecucion

Inicia primero el Edge API y luego ejecuta:

```powershell
python mock_service.py
```

Cada tick envia `POST /api/v1/telemetry`. Las respuestas `200` y `204` se
consideran exitosas. Si una respuesta `200` incluye `commands`, el simulador
aplica comandos validos para `water_pump` y `fertilizer_pump`.
