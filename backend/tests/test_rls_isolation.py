"""Row-Level Security isolation — proves the DB-level backstop actually filters.

Creates two tenants with a chat each (under bypass), then asserts that with
`app.current_tenant` set to tenant A, A's chat is visible and B's is NOT — i.e.
even a query with no app-level tenant filter cannot cross tenants. Also asserts
the cleared context bypasses (so superadmin / background paths keep working).
"""
import uuid

from sqlalchemy import text

from app.core.database import async_session
from app.core.rls import set_tenant_context


def test_rls_blocks_cross_tenant_reads(event_loop):
    a_id = uuid.uuid4()
    b_id = uuid.uuid4()
    a_chat = uuid.uuid4()
    b_chat = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]

    async def _scenario():
        async with async_session() as db:
            # Seed under bypass (no context set yet).
            for tid, slug in ((a_id, f"rls-a-{suffix}"), (b_id, f"rls-b-{suffix}")):
                await db.execute(text(
                    "INSERT INTO tenants (id, name, slug, is_active, created_at, updated_at) "
                    "VALUES (:id, :name, :slug, true, NOW(), NOW())"
                ), {"id": tid, "name": slug, "slug": slug})
            for cid, tid in ((a_chat, a_id), (b_chat, b_id)):
                await db.execute(text(
                    "INSERT INTO chats (id, tenant_id, title, status, created_at, updated_at) "
                    "VALUES (:id, :tid, 'rls test', 'active', NOW(), NOW())"
                ), {"id": cid, "tid": tid})
            await db.commit()

        results = {}
        try:
            # Context = tenant A → only A's chat visible.
            async with async_session() as db:
                await set_tenant_context(db, a_id)
                rows = (await db.execute(text(
                    "SELECT id FROM chats WHERE id = ANY(:ids)"
                ), {"ids": [a_chat, b_chat]})).scalars().all()
                results["ctx_a"] = {str(r) for r in rows}

            # Context = tenant B → only B's chat visible.
            async with async_session() as db:
                await set_tenant_context(db, b_id)
                rows = (await db.execute(text(
                    "SELECT id FROM chats WHERE id = ANY(:ids)"
                ), {"ids": [a_chat, b_chat]})).scalars().all()
                results["ctx_b"] = {str(r) for r in rows}

            # Cleared context → bypass, both visible.
            async with async_session() as db:
                await set_tenant_context(db, None)
                rows = (await db.execute(text(
                    "SELECT id FROM chats WHERE id = ANY(:ids)"
                ), {"ids": [a_chat, b_chat]})).scalars().all()
                results["bypass"] = {str(r) for r in rows}
        finally:
            async with async_session() as db:
                await db.execute(text("DELETE FROM chats WHERE id = ANY(:ids)"),
                                 {"ids": [a_chat, b_chat]})
                await db.execute(text("DELETE FROM tenants WHERE id = ANY(:ids)"),
                                 {"ids": [a_id, b_id]})
                await db.commit()
        return results

    res = event_loop.run_until_complete(_scenario())

    assert res["ctx_a"] == {str(a_chat)}, "tenant A context must see only A's chat"
    assert res["ctx_b"] == {str(b_chat)}, "tenant B context must see only B's chat"
    assert res["bypass"] == {str(a_chat), str(b_chat)}, "cleared context must bypass"
