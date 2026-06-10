#!/usr/bin/env python3
"""Apply `selectable_fields` to top query tools so LLM can request only
specific columns to reduce output size."""
import asyncio
import sys

import psycopg2

DB_URL = "postgresql://ai_platform:ai_platform_secret@localhost:5432/ai_platform"
TENANT_ID = "403d219f-0f4a-4782-a884-0e25f8bfe241"

# Per-tool selectable field lists. Picked from actual tool result columns
# observed via `search_clients` probe; for the others we use conservative
# guesses based on common patterns — admin can refine later via SQL/UI.
SELECTABLE: dict[str, list[str]] = {
    # Confirmed via live probe
    "search_clients": [
        "id", "name", "phone", "sms_phone", "amount", "kredit",
        "dogovor_num", "street", "house", "apart", "litera",
    ],
    # Other search_* tools — common fields (admin can refine)
    "search_services": [
        "id", "name", "client_id", "client_name", "status", "rate", "amount",
        "service_name", "phone", "ip", "mac", "switch_id", "port_index",
    ],
    "search_dev_by_mac": [
        "id", "mac", "name", "ip", "switch_id", "port_index", "client_id",
        "client_name", "status",
    ],
    "search_dhcp_lease": [
        "ip", "mac", "client_id", "client_name", "expires_at", "starts_at",
        "switch_id", "port_index", "hostname",
    ],
    "search_addresses": [
        "id", "street", "house", "litera", "apart", "client_id", "client_name",
        "phone", "switch_id",
    ],
}


def main(apply: bool):
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        for name, fields in SELECTABLE.items():
            cur.execute(
                "SELECT id, config_json FROM tenant_tools "
                "WHERE tenant_id=%s AND name=%s AND deleted_at IS NULL",
                (TENANT_ID, name),
            )
            row = cur.fetchone()
            if not row:
                print(f"  {name}: NOT FOUND for tenant")
                continue
            tool_id, cfg = row
            cfg = dict(cfg or {})
            runtime = dict(cfg.get("x_backend_config") or {})
            existing = runtime.get("selectable_fields")
            if existing == fields:
                print(f"  {name}: already configured ({len(fields)} fields)")
                continue
            runtime["selectable_fields"] = fields
            cfg["x_backend_config"] = runtime
            if apply:
                import json
                cur.execute(
                    "UPDATE tenant_tools SET config_json=%s, updated_at=now() WHERE id=%s",
                    (json.dumps(cfg), tool_id),
                )
                print(f"  ✅ {name}: {len(fields)} fields configured")
            else:
                print(f"  [DRY] {name}: would set {len(fields)} fields")
        if apply:
            conn.commit()
            print("\n✅ committed.")
        else:
            print("\nPass --apply to commit.")
    finally:
        conn.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
