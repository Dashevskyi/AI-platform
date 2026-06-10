"""Human-in-the-loop confirmation: a requires_confirmation command parks a
pending action instead of running, and reject/approve transition it."""
import uuid

from sqlalchemy import text

from app.core.database import async_session
from app.core.rls import set_tenant_context
from app.services.tools.executor import execute_tool, _resolve_command
from app.services.tools import pending as pending_svc


def test_resolve_command_surfaces_confirmation_flag():
    cfg = {"commands": {"reboot": {"command": "reboot", "write": True, "requires_confirmation": True}}}
    r = _resolve_command(cfg, {"command_name": "reboot"})
    assert r.requires_confirmation is True
    assert r.is_write is True


def _ssh_tool_config(tenant_id, chat_id):
    return {
        "function": {"name": "switch_reboot"},
        "x_backend_config": {
            "host": "192.0.2.1",  # TEST-NET-1, never actually connected (gate fires first)
            "username": "x", "password": "y",
            "allow_write": True,  # so the write-guard doesn't pre-empt the confirmation gate
            "commands": {"reboot": {"command": "reboot", "write": True, "requires_confirmation": True}},
        },
        "_context": {"tenant_id": str(tenant_id), "chat_id": str(chat_id)},
    }


def test_gate_creates_pending_then_reject(event_loop):
    tid, cid = uuid.uuid4(), uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async def _scenario():
        async with async_session() as db:
            await db.execute(text(
                "INSERT INTO tenants (id, name, slug, is_active, created_at, updated_at) "
                "VALUES (:id,:n,:s,true,now(),now())"), {"id": tid, "n": f"hitl-{suffix}", "s": f"hitl-{suffix}"})
            await db.execute(text(
                "INSERT INTO chats (id, tenant_id, title, status, created_at, updated_at) "
                "VALUES (:id,:t,'hitl','active',now(),now())"), {"id": cid, "t": tid})
            await db.commit()
        try:
            # requires_confirmation command must NOT execute — it parks a pending action.
            result = await execute_tool("ssh_exec", {"command_name": "reboot", "params": {}}, _ssh_tool_config(tid, cid))
            async with async_session() as db:
                rows = (await db.execute(text(
                    "SELECT id, status, tool_name, command_name FROM pending_tool_actions WHERE chat_id=:c"
                ), {"c": cid})).mappings().all()
            outcome = {"output": result.output, "rows": [dict(r) for r in rows]}

            # Reject the parked action.
            if rows:
                async with async_session() as db:
                    # transaction-local context (is_local=true) — must not leak to
                    # other tests via the pooled connection.
                    await set_tenant_context(db, tid)
                    action = await pending_svc.reject(db, tid, cid, rows[0]["id"], "test")
                outcome["rejected_status"] = action.status
            return outcome
        finally:
            async with async_session() as db:
                await db.execute(text("DELETE FROM pending_tool_actions WHERE chat_id=:c"), {"c": cid})
                await db.execute(text("DELETE FROM chats WHERE id=:c"), {"c": cid})
                await db.execute(text("DELETE FROM tenants WHERE id=:t"), {"t": tid})
                await db.commit()

    out = event_loop.run_until_complete(_scenario())
    assert "подтвержд" in out["output"].lower()      # the model is told confirmation is needed
    assert len(out["rows"]) == 1
    assert out["rows"][0]["status"] == "pending"
    assert out["rows"][0]["tool_name"] == "switch_reboot"   # function name, not handler name
    assert out["rows"][0]["command_name"] == "reboot"
    assert out["rejected_status"] == "rejected"
