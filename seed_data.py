#!/usr/bin/env python3
"""
Historical seeding is intentionally disabled.

The current AquaEdge mock must not create tables, seed old schemas, or use the
Supabase SDK. Run `python mock_service.py` to generate live telemetry through
the existing Prisma-managed PostgreSQL schema.
"""


def main() -> None:
    print("Historical seed disabled for the Prisma/PostgreSQL integration.")
    print("Use: python mock_service.py")


if __name__ == "__main__":
    main()
