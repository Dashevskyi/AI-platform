from sqlalchemy.ext.asyncio import AsyncSession


async def log_audit(
    db: AsyncSession,
    actor_id: str,
    actor_role: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    tenant_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """Write an entry to the admin audit log."""
    from app.models import AdminAuditLog

    log = AdminAuditLog(
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        tenant_id=tenant_id,
        before_json=before,
        after_json=after,
    )
    db.add(log)
