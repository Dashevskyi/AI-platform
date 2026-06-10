#!/usr/bin/env python3
"""Configure Tier 0 for search_clients (proof of concept) and enable on IT-Invest."""
import asyncio
import json
import sys

import psycopg2

from app.core.database import async_session
from sqlalchemy import select
from app.models.tenant_tool import TenantTool

DB_URL = "postgresql://ai_platform:ai_platform_secret@localhost:5432/ai_platform"
TENANT_ID = "403d219f-0f4a-4782-a884-0e25f8bfe241"

TIER0_TEMPLATE = {
    "template": (
        "**{items.0.name}** (договор №{items.0.dogovor_num})\n"
        "- Телефон: {items.0.phone}\n"
        "- Адрес: {items.0.street} {items.0.house}, кв. {items.0.apart}\n"
        "- Баланс: {items.0.amount} грн (кредит: {items.0.kredit})"
    ),
    "required_entity": "phone",
    "param_map": {
        # Strip the +38 prefix that our extractor adds — search_clients
        # wants the bare 0XXXXXXXXX Ukrainian format.
        "filters.phone": "$phone|re_sub:^\\+38=>",
        "limit": 1,
    },
    "required_fields": [
        "items.0.name",
        "items.0.amount",
        "items.0.phone",
    ],
}


async def configure_tool():
    async with async_session() as db:
        tool = (await db.execute(
            select(TenantTool).where(
                TenantTool.name == "search_clients",
                TenantTool.tenant_id == TENANT_ID,
            )
        )).scalar_one()
        cfg = dict(tool.config_json or {})
        runtime = dict(cfg.get("x_backend_config") or {})
        runtime["tier0_template"] = TIER0_TEMPLATE
        cfg["x_backend_config"] = runtime
        tool.config_json = cfg
        await db.commit()
        print("✅ search_clients.x_backend_config.tier0_template configured")
        print("   template:", TIER0_TEMPLATE["template"][:80], "...")


def enable_tier0_on_tenant():
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tenant_shell_configs SET tier0_enabled=true WHERE tenant_id=%s",
            (TENANT_ID,),
        )
        conn.commit()
        print(f"✅ tier0_enabled=true for tenant {TENANT_ID[:8]}…")
    finally:
        conn.close()


async def main():
    await configure_tool()
    enable_tier0_on_tenant()
    print("\nDone. Backend will pick up changes on next request (config is read per-request).")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
