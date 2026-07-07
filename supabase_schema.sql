-- AquaEdge Supabase Schema
-- Run this in the Supabase Dashboard → SQL Editor → New query

-- ---------------------------------------------------------------------------
-- Devices registry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT UNIQUE NOT NULL,
    name TEXT,
    firmware_version TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- Telemetry readings (one row per 5-second tick)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    tick_count INTEGER NOT NULL,
    timestamp_utc TIMESTAMPTZ,
    wifi_rssi_dbm INTEGER,
    firmware_version TEXT,

    -- Sensors
    soil_moisture_value REAL,
    soil_moisture_raw_adc INTEGER,
    soil_moisture_is_valid BOOLEAN,

    soil_fertility_value REAL,
    soil_fertility_raw_adc INTEGER,
    soil_fertility_is_valid BOOLEAN,

    soil_temperature_value REAL,
    soil_temperature_is_valid BOOLEAN,

    air_temperature REAL,
    air_humidity REAL,
    air_is_valid BOOLEAN,

    water_level_status TEXT,
    water_level_is_valid BOOLEAN,

    -- Diagnosis
    needs_irrigation BOOLEAN,
    needs_fertilization BOOLEAN,
    alert_message TEXT,

    -- Actuators
    water_pump_state TEXT,
    fertilizer_pump_state TEXT,

    -- Health
    system_health_overall TEXT,
    failed_sensors JSONB DEFAULT '[]',
    pending_commands JSONB DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_telemetry_device_time
    ON telemetry(device_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_tick
    ON telemetry(device_id, tick_count DESC);

-- ---------------------------------------------------------------------------
-- Pending commands (edge → device, piggybacked in telemetry response)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_commands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    device_id TEXT NOT NULL,
    target TEXT NOT NULL CHECK (target IN ('water_pump', 'fertilizer_pump')),
    state TEXT NOT NULL CHECK (state IN ('ON', 'OFF')),
    duration_sec INTEGER NOT NULL DEFAULT 10 CHECK (duration_sec BETWEEN 1 AND 300),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'executed', 'rejected')),
    delivered_at TIMESTAMPTZ,
    executed_at TIMESTAMPTZ,
    result_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_commands_device_status
    ON pending_commands(device_id, status, created_at);

-- ---------------------------------------------------------------------------
-- Row Level Security (RLS)
-- ---------------------------------------------------------------------------
ALTER TABLE devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE telemetry ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_commands ENABLE ROW LEVEL SECURITY;

-- Devices: anyone can read, only service_role can insert/update
CREATE POLICY devices_select_all ON devices
    FOR SELECT USING (true);

-- Telemetry: anyone can read, only service_role can insert
CREATE POLICY telemetry_select_all ON telemetry
    FOR SELECT USING (true);

-- Pending commands: anyone can read their own device's commands, insert via service_role
CREATE POLICY pending_commands_select_device ON pending_commands
    FOR SELECT USING (true);

-- ---------------------------------------------------------------------------
-- Realtime (enable these tables for live WebSocket subscriptions)
-- ---------------------------------------------------------------------------
-- In Supabase Dashboard → Database → Replication, add these tables to the
-- `supabase_realtime` publication, or run:
--   ALTER PUBLICATION supabase_realtime ADD TABLE telemetry;
--   ALTER PUBLICATION supabase_realtime ADD TABLE pending_commands;
-- Note: This may already be enabled if you toggle the switch in the UI.

-- ---------------------------------------------------------------------------
-- Seed one default device (optional)
-- ---------------------------------------------------------------------------
INSERT INTO devices (device_id, name, firmware_version, last_seen_at)
VALUES ('aquaedge-01', 'Development Mock', 'mock-1.0.0', NOW())
ON CONFLICT (device_id) DO NOTHING;
