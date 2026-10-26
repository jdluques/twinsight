"""Reglas de comportamiento (RF-055..RF-059): CRUD y dry-run.

La validación del esquema de regla se delega en ``validate_rule``; el dry-run
en ``test_rule`` (importado con alias para que pytest no lo recoja como test).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..access import Principal, require_editor, require_viewer
from ..behavior import test_rule as dry_run_rule
from ..behavior import validate_rule
from ..persistence.db import get_session
from ..persistence.models import RuleDef
from .common import not_found, project_or_404, unprocessable
from .schemas import RuleCreate, RuleTest, RuleUpdate

router = APIRouter(tags=["rules"])


def _rule(rule: RuleDef) -> dict:
    return {
        "id": rule.id,
        "project_id": rule.project_id,
        "name": rule.name,
        "enabled": rule.enabled,
        "severity": rule.severity,
        "definition": rule.definition or {},
    }


async def _rule_or_404(session: AsyncSession, rule_id: str) -> RuleDef:
    rule = await session.get(RuleDef, rule_id)
    if rule is None:
        raise not_found(f"regla no encontrada: {rule_id}")
    return rule


@router.get("/projects/{project_id}/rules")
async def list_rules(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> list[dict]:
    await project_or_404(session, project_id)
    rows = (
        await session.execute(select(RuleDef).where(RuleDef.project_id == project_id))
    ).scalars()
    return [_rule(r) for r in rows]


@router.post("/projects/{project_id}/rules", status_code=201)
async def create_rule(
    project_id: str,
    body: RuleCreate,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    await project_or_404(session, project_id)
    try:
        validate_rule(body.definition)
    except ValueError as exc:
        raise unprocessable(f"definición de regla inválida: {exc}", "invalid_rule")
    rule = RuleDef(
        project_id=project_id,
        name=body.name,
        enabled=body.enabled,
        severity=body.severity,
        definition=body.definition,
    )
    session.add(rule)
    await session.commit()
    return _rule(rule)


@router.get("/rules/{rule_id}")
async def get_rule(
    rule_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_viewer),
) -> dict:
    return _rule(await _rule_or_404(session, rule_id))


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    body: RuleUpdate,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    rule = await _rule_or_404(session, rule_id)
    if body.definition is not None:
        try:
            validate_rule(body.definition)
        except ValueError as exc:
            raise unprocessable(f"definición de regla inválida: {exc}", "invalid_rule")
        rule.definition = body.definition
    if body.name is not None:
        rule.name = body.name
    if body.enabled is not None:
        rule.enabled = body.enabled
    if body.severity is not None:
        rule.severity = body.severity
    await session.commit()
    return _rule(rule)


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    rule = await _rule_or_404(session, rule_id)
    await session.delete(rule)
    await session.commit()
    return {"status": "deleted", "rule_id": rule_id}


@router.post("/rules/{rule_id}/test")
async def test_rule_endpoint(
    rule_id: str,
    body: RuleTest,
    session: AsyncSession = Depends(get_session),
    _p: Principal = Depends(require_editor),
) -> dict:
    """Dry-run de la regla almacenada contra ``last_values`` (RF-058)."""
    rule = await _rule_or_404(session, rule_id)
    try:
        return dry_run_rule(rule.definition or {}, body.last_values)
    except ValueError as exc:
        raise unprocessable(f"definición de regla inválida: {exc}", "invalid_rule")
