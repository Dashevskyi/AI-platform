"""Durable background-job queue: enqueue → claim → run, with retry on failure."""
import uuid

from sqlalchemy import text

from app.core.database import async_session
from app.services.jobs import queue


def test_job_succeeds_and_failure_retries(event_loop):
    seen = {"ok": 0, "fail": 0}

    @queue.register_job("_t_ok")
    async def _ok(payload):
        seen["ok"] += 1

    @queue.register_job("_t_fail")
    async def _fail(payload):
        seen["fail"] += 1
        raise RuntimeError("boom")

    async def _scenario():
        async with async_session() as db:
            await queue.enqueue(db, "_t_ok", {"v": 1})
            await queue.enqueue(db, "_t_fail", {}, max_attempts=2)
            await db.commit()
        handled = 0
        for _ in range(5):
            if await queue._run_one():
                handled += 1
            else:
                break
        async with async_session() as db:
            rows = {
                r._mapping["job_type"]: r._mapping
                for r in (await db.execute(text(
                    "SELECT job_type, status, attempts, run_after > now() AS scheduled "
                    "FROM background_jobs WHERE job_type IN ('_t_ok','_t_fail')"
                ))).all()
            }
            await db.execute(text("DELETE FROM background_jobs WHERE job_type IN ('_t_ok','_t_fail')"))
            await db.commit()
        return handled, rows

    handled, rows = event_loop.run_until_complete(_scenario())

    assert handled == 2
    assert seen == {"ok": 1, "fail": 1}
    # Successful job is terminal.
    assert rows["_t_ok"]["status"] == "succeeded"
    # Failed job (retries left) is requeued with a future run_after (backoff).
    assert rows["_t_fail"]["status"] == "pending"
    assert rows["_t_fail"]["attempts"] == 1
    assert rows["_t_fail"]["scheduled"] is True


def test_failure_exhausts_attempts(event_loop):
    @queue.register_job("_t_always_fail")
    async def _f(payload):
        raise RuntimeError("nope")

    async def _scenario():
        async with async_session() as db:
            await queue.enqueue(db, "_t_always_fail", {}, max_attempts=1)
            await db.commit()
        await queue._run_one()
        async with async_session() as db:
            row = (await db.execute(text(
                "SELECT status, last_error FROM background_jobs WHERE job_type='_t_always_fail'"
            ))).mappings().first()
            await db.execute(text("DELETE FROM background_jobs WHERE job_type='_t_always_fail'"))
            await db.commit()
        return row

    row = event_loop.run_until_complete(_scenario())
    assert row["status"] == "failed"
    assert "nope" in (row["last_error"] or "")
