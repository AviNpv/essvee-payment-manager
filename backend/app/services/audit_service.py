from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AuditLog


def log(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    actor_id: UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = False,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        before=_jsonable(before),
        after=_jsonable(after),
        actor_id=actor_id,
        actor_email=actor_email,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
    if commit:
        db.commit()
    else:
        db.flush()
    return entry


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "hex"):
        return str(value)
    return str(value)
