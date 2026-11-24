from __future__ import annotations

import secrets
from datetime import datetime, timezone
from math import ceil
from typing import Any, Literal

from app.models import (
    Check,
    Guideline,
    GuidelineLink,
    OrgUnit,
    Rule,
    RuleSet,
    RuleType,
    UploadedCheck,
    Violation,
)
from app.rule_types import RULE_TYPE_CATALOGUE
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ValidationError
from rule_engine.rules import RuleSet as EngineRuleSet
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ORG_KIND_LEVELS = {"university": 0, "institute": 1, "department": 2}
MAX_PAGE_SIZE = 100
DEFAULT_RULE_SCOPES = {
    spec.code: spec.default_scope for spec in RULE_TYPE_CATALOGUE
}


bearer_scheme = HTTPBearer(auto_error=False)


def require_admin_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    expected = request.app.state.admin_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Административная авторизация не настроена",
        )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется токен администратора",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(
        expected.encode("utf-8"), credentials.credentials.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный токен администратора",
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(
    tags=["Администрирование"],
    dependencies=[Depends(require_admin_token)],
)


class PageResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    pages: int


class OrgUnitCreate(BaseModel):
    kind: Literal["university", "institute", "department"]
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None
    code: str | None = Field(default=None, max_length=50)
    profile_name: str | None = Field(default=None, max_length=255)


class OrgUnitUpdate(BaseModel):
    kind: Literal["university", "institute", "department"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: int | None = None
    code: str | None = Field(default=None, max_length=50)
    profile_name: str | None = Field(default=None, max_length=255)


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    params: dict[str, Any] | None = None
    scope: str | None = Field(default=None, max_length=50)


class RuleDecision(BaseModel):
    decision: Literal["approved", "rejected"]


class RuleSetStatusUpdate(BaseModel):
    status: Literal["active", "archived"]


class BulkApproval(BaseModel):
    confidence_threshold: float = Field(ge=0, le=1)


class AnalyticsResponse(BaseModel):
    checks_by_status: dict[str, int]
    checks_by_day: list[dict[str, Any]]
    failure_rate_by_day: list[dict[str, Any]]
    pending_review_rulesets: int
    rulesets_by_status: dict[str, int]
    guideline_count: int
    rule_count: int
    common_violations: list[dict[str, Any]]
    violations_by_org_unit: list[dict[str, Any]]


def get_session(request: Request):
    session_factory = request.app.state.session_factory
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Хранилище базы данных не настроено",
        )
    with session_factory() as session:
        yield session


def _page(
    session: Session,
    statement,
    count_statement,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    total = int(session.scalar(count_statement) or 0)
    items = session.execute(
        statement.offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [dict(row._mapping) for row in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": ceil(total / page_size) if total else 0,
    }


def _validate_rule_params(
    rule_type: RuleType, params: dict[str, Any] | None, scope: str | None
) -> None:
    payload = {
        "ruleset_id": "admin-validation",
        "ruleset_version": "1",
        "rules": [
            {
                "rule_id": f"admin-{rule_type.code}",
                "code": rule_type.code,
                "message": "Валидация параметров администратором",
                "params": params or {},
                "target": {
                    "scope": scope
                    or DEFAULT_RULE_SCOPES.get(rule_type.code, "body_text")
                },
            }
        ],
    }
    try:
        EngineRuleSet.model_validate(payload)
    except (ValidationError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Некорректные параметры правила: {exc}",
        ) from exc


def _validate_org_hierarchy(
    session: Session,
    kind: str,
    parent_id: int | None,
    unit_id: int | None = None,
) -> OrgUnit | None:
    if parent_id is None:
        if kind != "university":
            raise HTTPException(
                status_code=422,
                detail="Корневым узлом справочника может быть только университет",
            )
        return None

    parent = session.get(OrgUnit, parent_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="Родительский узел не найден")
    if parent.kind not in ORG_KIND_LEVELS or kind not in ORG_KIND_LEVELS:
        raise HTTPException(status_code=422, detail="Неизвестный тип узла")
    if ORG_KIND_LEVELS[kind] <= ORG_KIND_LEVELS[parent.kind]:
        raise HTTPException(
            status_code=422,
            detail="Тип дочернего узла должен быть ниже типа родительского",
        )
    if unit_id is not None:
        current = parent
        while current is not None:
            if current.id == unit_id:
                raise HTTPException(
                    status_code=422,
                    detail="Нельзя сделать узел потомком самого себя",
                )
            current = (
                session.get(OrgUnit, current.parent_id)
                if current.parent_id is not None
                else None
            )
    return parent


def _new_ruleset_version(session: Session, source: RuleSet) -> RuleSet:
    max_version = session.scalar(
        select(func.max(RuleSet.version)).where(
            RuleSet.guideline_id == source.guideline_id
        )
    )
    child = RuleSet(
        guideline_id=source.guideline_id,
        parent_ruleset_id=source.id,
        name=source.name,
        version=(max_version or source.version) + 1,
        generation_method=source.generation_method,
        status="review",
        trust_level=source.trust_level,
    )
    session.add(child)
    session.flush()
    for source_rule in session.scalars(
        select(Rule).where(Rule.ruleset_id == source.id).order_by(Rule.id)
    ):
        session.add(
            Rule(
                ruleset_id=child.id,
                rule_type_id=source_rule.rule_type_id,
                inherited_from_rule_id=source_rule.id,
                name=source_rule.name,
                params=source_rule.params,
                scope=source_rule.scope,
                quote=source_rule.quote,
                quote_location=source_rule.quote_location,
                confidence=source_rule.confidence,
                status=source_rule.status,
                origin=source_rule.origin,
                target_element=source_rule.target_element,
                verification_method=source_rule.verification_method,
                strictness=source_rule.strictness,
            )
        )
    session.flush()
    return child


def _normalize_quote(quote: str | None) -> str:
    return " ".join((quote or "").split()).casefold()


def _find_approved_duplicate(session: Session, rule: Rule) -> Rule | None:
    if not rule.quote:
        return None
    candidates = session.scalars(
        select(Rule).where(
            Rule.id != rule.id,
            Rule.rule_type_id == rule.rule_type_id,
            Rule.scope == rule.scope,
            Rule.status == "approved",
            Rule.quote.is_not(None),
        )
    )
    for candidate in candidates:
        if (
            _normalize_quote(candidate.quote) == _normalize_quote(rule.quote)
            and candidate.params == rule.params
        ):
            return candidate
    return None


@router.get("/org-units", response_model=PageResponse)
def list_org_units(
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    search: str | None = None,
    kind: Literal["university", "institute", "department"] | None = None,
):
    statement = select(
        OrgUnit.id.label("id"),
        OrgUnit.kind.label("kind"),
        OrgUnit.name.label("name"),
        OrgUnit.parent_id.label("parent_id"),
        OrgUnit.code.label("code"),
        OrgUnit.profile_name.label("profile_name"),
    ).order_by(OrgUnit.kind, OrgUnit.name, OrgUnit.id)
    count_statement = select(func.count(OrgUnit.id))
    if search:
        criterion = OrgUnit.name.ilike(f"%{search.strip()}%")
        statement = statement.where(criterion)
        count_statement = count_statement.where(criterion)
    if kind:
        statement = statement.where(OrgUnit.kind == kind)
        count_statement = count_statement.where(OrgUnit.kind == kind)
    return _page(session, statement, count_statement, page, page_size)


@router.post("/org-units", status_code=201)
def create_org_unit(payload: OrgUnitCreate, session: Session = Depends(get_session)):
    _validate_org_hierarchy(session, payload.kind, payload.parent_id)
    unit = OrgUnit(**payload.model_dump())
    session.add(unit)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Узел справочника конфликтует с данными"
        ) from exc
    session.refresh(unit)
    return _org_unit_dict(unit)


def _org_unit_dict(unit: OrgUnit) -> dict[str, Any]:
    return {
        "id": unit.id,
        "kind": unit.kind,
        "name": unit.name,
        "parent_id": unit.parent_id,
        "code": unit.code,
        "profile_name": unit.profile_name,
    }


@router.patch("/org-units/{unit_id}")
def update_org_unit(
    unit_id: int,
    payload: OrgUnitUpdate,
    session: Session = Depends(get_session),
):
    unit = session.get(OrgUnit, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Узел справочника не найден")
    changes = payload.model_dump(exclude_unset=True)
    kind = changes.get("kind", unit.kind)
    parent_id = changes.get("parent_id", unit.parent_id)
    _validate_org_hierarchy(session, kind, parent_id, unit_id)
    if kind != unit.kind:
        for child in unit.children:
            if ORG_KIND_LEVELS.get(child.kind, -1) <= ORG_KIND_LEVELS[kind]:
                raise HTTPException(
                    status_code=422,
                    detail="Изменение типа нарушит иерархию дочерних узлов",
                )
    for key, value in changes.items():
        setattr(unit, key, value)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Узел справочника конфликтует с данными"
        ) from exc
    session.refresh(unit)
    return _org_unit_dict(unit)


@router.delete("/org-units/{unit_id}", status_code=204)
def delete_org_unit(unit_id: int, session: Session = Depends(get_session)):
    unit = session.get(OrgUnit, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Узел справочника не найден")
    if unit.children or session.scalar(
        select(func.count(GuidelineLink.id)).where(GuidelineLink.org_unit_id == unit_id)
    ):
        raise HTTPException(status_code=409, detail="Нельзя удалить используемый узел")
    session.delete(unit)
    session.commit()


@router.get("/guidelines", response_model=PageResponse)
def list_guidelines(
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    search: str | None = None,
    year: int | None = None,
    org_unit_id: int | None = None,
    confirmed: bool | None = None,
):
    query = (
        select(
            Guideline.id.label("id"),
            Guideline.title.label("title"),
            Guideline.year.label("year"),
            Guideline.confirmed.label("confirmed"),
            Guideline.relation_type.label("relation_type"),
            GuidelineLink.org_unit_id.label("org_unit_id"),
            GuidelineLink.direction_code.label("direction_code"),
            GuidelineLink.direction_name.label("direction_name"),
            GuidelineLink.program_code.label("program_code"),
            GuidelineLink.program_name.label("program_name"),
        )
        .outerjoin(GuidelineLink, GuidelineLink.guideline_id == Guideline.id)
        .order_by(Guideline.year.desc().nullslast(), Guideline.title, Guideline.id)
    )
    count_query = select(func.count(Guideline.id)).outerjoin(
        GuidelineLink, GuidelineLink.guideline_id == Guideline.id
    )
    if search:
        criterion = Guideline.title.ilike(f"%{search.strip()}%")
        query = query.where(criterion)
        count_query = count_query.where(criterion)
    if year is not None:
        query = query.where(Guideline.year == year)
        count_query = count_query.where(Guideline.year == year)
    if org_unit_id is not None:
        query = query.where(GuidelineLink.org_unit_id == org_unit_id)
        count_query = count_query.where(GuidelineLink.org_unit_id == org_unit_id)
    if confirmed is not None:
        query = query.where(Guideline.confirmed == confirmed)
        count_query = count_query.where(Guideline.confirmed == confirmed)
    result = _page(session, query, count_query, page, page_size)
    result["items"] = [dict(item) for item in result["items"]]
    return result


@router.get("/review-queue", response_model=PageResponse)
def list_review_queue(
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
):
    statement = (
        select(
            RuleSet.id.label("id"),
            RuleSet.guideline_id.label("guideline_id"),
            Guideline.title.label("guideline_title"),
            RuleSet.name.label("name"),
            RuleSet.version.label("version"),
            func.count(Rule.id).label("rule_count"),
            func.sum(case((Rule.status.in_(("review", "draft")), 1), else_=0)).label(
                "unreviewed_count"
            ),
        )
        .join(Guideline, Guideline.id == RuleSet.guideline_id)
        .outerjoin(Rule, Rule.ruleset_id == RuleSet.id)
        .where(RuleSet.status == "review")
        .group_by(RuleSet.id, Guideline.title)
        .order_by(RuleSet.id)
    )
    count_statement = select(func.count(RuleSet.id)).where(RuleSet.status == "review")
    return _page(session, statement, count_statement, page, page_size)


@router.get("/rulesets/{ruleset_id}/rules", response_model=PageResponse)
def list_rules(
    ruleset_id: int,
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    rule_status: Literal["draft", "review", "approved", "rejected"] | None = Query(
        default=None, alias="status"
    ),
):
    if session.get(RuleSet, ruleset_id) is None:
        raise HTTPException(status_code=404, detail="Набор правил не найден")
    statement = (
        select(
            Rule.id.label("id"),
            Rule.name.label("name"),
            RuleType.code.label("type"),
            RuleType.name.label("type_name"),
            Rule.params.label("params"),
            Rule.scope.label("scope"),
            Rule.quote.label("quote"),
            Rule.quote_location.label("quote_location"),
            Rule.confidence.label("confidence"),
            Rule.origin.label("origin"),
            Rule.status.label("status"),
            Rule.inherited_from_rule_id.label("inherited_from_rule_id"),
        )
        .outerjoin(RuleType, RuleType.id == Rule.rule_type_id)
        .where(Rule.ruleset_id == ruleset_id)
        .order_by(Rule.confidence.asc().nullslast(), Rule.id)
    )
    count_statement = select(func.count(Rule.id)).where(Rule.ruleset_id == ruleset_id)
    if rule_status:
        statement = statement.where(Rule.status == rule_status)
        count_statement = count_statement.where(Rule.status == rule_status)
    return _page(session, statement, count_statement, page, page_size)


@router.get("/rulesets/{ruleset_id}")
def get_ruleset(ruleset_id: int, session: Session = Depends(get_session)):
    ruleset = session.get(RuleSet, ruleset_id)
    if ruleset is None:
        raise HTTPException(status_code=404, detail="Набор правил не найден")
    rules = session.execute(
        select(
            func.count(Rule.id).label("total"),
            func.sum(case((Rule.status == "approved", 1), else_=0)).label("approved"),
            func.sum(case((Rule.status == "rejected", 1), else_=0)).label("rejected"),
            func.sum(case((Rule.status.in_(("review", "draft")), 1), else_=0)).label(
                "unreviewed"
            ),
        ).where(Rule.ruleset_id == ruleset_id)
    ).one()
    return {
        "id": ruleset.id,
        "guideline_id": ruleset.guideline_id,
        "parent_ruleset_id": ruleset.parent_ruleset_id,
        "name": ruleset.name,
        "version": ruleset.version,
        "status": ruleset.status,
        "counts": {
            "total": rules.total or 0,
            "approved": rules.approved or 0,
            "rejected": rules.rejected or 0,
            "unreviewed": rules.unreviewed or 0,
        },
    }


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    payload: RuleUpdate,
    session: Session = Depends(get_session),
):
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    ruleset = session.get(RuleSet, rule.ruleset_id)
    if ruleset is None:
        raise HTTPException(status_code=409, detail="Набор правила не найден")
    cloned = False
    if ruleset.status in {"active", "approved", "archived"}:
        original_rule_id = rule.id
        try:
            ruleset = _new_ruleset_version(session, ruleset)
            cloned = True
            rule = session.scalar(
                select(Rule).where(
                    Rule.ruleset_id == ruleset.id,
                    Rule.inherited_from_rule_id == original_rule_id,
                )
            )
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=409, detail="Конфликт версии набора"
            ) from exc
    if "params" in payload.model_fields_set or "scope" in payload.model_fields_set:
        rule_type = session.get(RuleType, rule.rule_type_id)
        if rule_type is None:
            raise HTTPException(
                status_code=422, detail="У правила не задан известный тип"
            )
        params = payload.params if "params" in payload.model_fields_set else rule.params
        scope = payload.scope if "scope" in payload.model_fields_set else rule.scope
        _validate_rule_params(
            rule_type,
            params,
            scope,
        )
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(rule, key, value)
    if changes:
        rule.status = "review"
        ruleset.status = "review"
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Конфликт версии набора") from exc
    return {"rule_id": rule.id, "ruleset_id": ruleset.id, "status": rule.status}


@router.post("/rules/{rule_id}/decision")
def decide_rule(
    rule_id: int,
    payload: RuleDecision,
    session: Session = Depends(get_session),
):
    rule = session.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    ruleset = session.get(RuleSet, rule.ruleset_id)
    if ruleset is None or ruleset.status != "review":
        raise HTTPException(status_code=409, detail="Набор не ожидает проверки")
    if rule.status not in {"review", "draft"}:
        raise HTTPException(status_code=409, detail="Правило уже рассмотрено")
    rule.status = payload.decision
    if payload.decision == "approved":
        duplicate = _find_approved_duplicate(session, rule)
        if duplicate is not None:
            rule.inherited_from_rule_id = duplicate.id
    session.flush()
    remaining = session.scalar(
        select(func.count(Rule.id)).where(
            Rule.ruleset_id == ruleset.id,
            Rule.status.in_(("review", "draft")),
        )
    )
    if not remaining:
        approved_count = session.scalar(
            select(func.count(Rule.id)).where(
                Rule.ruleset_id == ruleset.id,
                Rule.status == "approved",
            )
        )
        ruleset.status = "approved" if approved_count else "rejected"
    session.commit()
    return {"rule_id": rule.id, "status": rule.status, "ruleset_status": ruleset.status}


@router.post("/rulesets/{ruleset_id}/approve-by-confidence")
def bulk_approve_rules(
    ruleset_id: int,
    payload: BulkApproval,
    session: Session = Depends(get_session),
):
    ruleset = session.get(RuleSet, ruleset_id)
    if ruleset is None:
        raise HTTPException(status_code=404, detail="Набор правил не найден")
    if ruleset.status != "review":
        raise HTTPException(status_code=409, detail="Набор не ожидает проверки")
    candidates = list(
        session.scalars(
            select(Rule).where(
                Rule.ruleset_id == ruleset_id,
                Rule.status.in_(("review", "draft")),
                Rule.confidence.is_not(None),
                Rule.confidence >= payload.confidence_threshold,
            )
        )
    )
    for rule in candidates:
        rule.status = "approved"
        duplicate = _find_approved_duplicate(session, rule)
        if duplicate is not None:
            rule.inherited_from_rule_id = duplicate.id
    session.flush()
    remaining = session.scalar(
        select(func.count(Rule.id)).where(
            Rule.ruleset_id == ruleset.id,
            Rule.status.in_(("review", "draft")),
        )
    )
    if not remaining:
        approved_count = session.scalar(
            select(func.count(Rule.id)).where(
                Rule.ruleset_id == ruleset.id,
                Rule.status == "approved",
            )
        )
        ruleset.status = "approved" if approved_count else "rejected"
    session.commit()
    return {
        "approved_count": len(candidates),
        "ruleset_status": ruleset.status,
    }


@router.post("/rulesets/{ruleset_id}/activate")
def activate_ruleset(ruleset_id: int, session: Session = Depends(get_session)):
    ruleset = session.scalar(
        select(RuleSet).where(RuleSet.id == ruleset_id).with_for_update()
    )
    if ruleset is None:
        raise HTTPException(status_code=404, detail="Набор правил не найден")
    if ruleset.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Активировать можно только полностью рассмотренный набор",
        )
    approved_count = session.scalar(
        select(func.count(Rule.id)).where(
            Rule.ruleset_id == ruleset_id,
            Rule.status == "approved",
        )
    )
    unreviewed_count = session.scalar(
        select(func.count(Rule.id)).where(
            Rule.ruleset_id == ruleset_id,
            Rule.status.in_(("review", "draft")),
        )
    )
    if not approved_count or unreviewed_count:
        raise HTTPException(
            status_code=409,
            detail="Нужны рассмотренные правила и хотя бы одно подтверждённое",
        )
    session.execute(
        select(RuleSet.id)
        .where(
            RuleSet.guideline_id == ruleset.guideline_id,
            RuleSet.status == "active",
        )
        .with_for_update()
    )
    session.query(RuleSet).filter(
        RuleSet.guideline_id == ruleset.guideline_id,
        RuleSet.status == "active",
        RuleSet.id != ruleset.id,
    ).update({"status": "archived"}, synchronize_session=False)
    ruleset.status = "active"
    ruleset.confirmed_at = datetime.now(timezone.utc)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Для методички уже активирован набор"
        ) from exc
    return {"ruleset_id": ruleset.id, "status": ruleset.status}


@router.patch("/rulesets/{ruleset_id}/status")
def update_ruleset_status(
    ruleset_id: int,
    payload: RuleSetStatusUpdate,
    session: Session = Depends(get_session),
):
    if payload.status == "active":
        return activate_ruleset(ruleset_id, session)
    ruleset = session.get(RuleSet, ruleset_id)
    if ruleset is None:
        raise HTTPException(status_code=404, detail="Набор правил не найден")
    if ruleset.status == "active":
        session.execute(
            select(RuleSet.id)
            .where(
                RuleSet.guideline_id == ruleset.guideline_id,
                RuleSet.status == "active",
            )
            .with_for_update()
        )
    ruleset.status = "archived"
    session.commit()
    return {"ruleset_id": ruleset.id, "status": ruleset.status}


@router.get("/analytics", response_model=AnalyticsResponse)
def get_analytics(session: Session = Depends(get_session)):
    check_rows = session.execute(
        select(UploadedCheck.status, func.count()).group_by(UploadedCheck.status)
    ).all()
    checks_by_day = session.execute(
        select(
            func.date(UploadedCheck.created_at).label("day"),
            func.count().label("count"),
        )
        .group_by(func.date(UploadedCheck.created_at))
        .order_by(func.date(UploadedCheck.created_at))
    ).all()
    failure_rows = session.execute(
        select(
            func.date(UploadedCheck.created_at).label("day"),
            func.sum(case((UploadedCheck.status == "failed", 1), else_=0)).label(
                "failed"
            ),
            func.sum(
                case((UploadedCheck.status.in_(("failed", "completed")), 1), else_=0)
            ).label("terminal"),
        )
        .group_by(func.date(UploadedCheck.created_at))
        .order_by(func.date(UploadedCheck.created_at))
    ).all()
    ruleset_rows = session.execute(
        select(RuleSet.status, func.count()).group_by(RuleSet.status)
    ).all()
    common_violations = session.execute(
        select(
            RuleType.code.label("rule_type"),
            Rule.name.label("rule_name"),
            func.count(Violation.id).label("count"),
        )
        .join(Rule, Rule.id == Violation.rule_id)
        .join(RuleType, RuleType.id == Rule.rule_type_id)
        .group_by(RuleType.code, Rule.name)
        .order_by(func.count(Violation.id).desc())
        .limit(10)
    ).all()
    violations_by_org = session.execute(
        select(
            OrgUnit.id.label("org_unit_id"),
            OrgUnit.name.label("org_unit_name"),
            func.count(func.distinct(Check.id)).label("checks"),
            func.count(Violation.id).label("violations"),
        )
        .join(GuidelineLink, GuidelineLink.org_unit_id == OrgUnit.id)
        .join(RuleSet, RuleSet.guideline_id == GuidelineLink.guideline_id)
        .join(Check, Check.ruleset_id == RuleSet.id)
        .outerjoin(Violation, Violation.check_id == Check.id)
        .group_by(OrgUnit.id, OrgUnit.name)
        .order_by(func.count(Violation.id).desc())
    ).all()
    return {
        "checks_by_status": {row[0]: row[1] for row in check_rows},
        "checks_by_day": [
            {"day": row.day.isoformat(), "count": row.count} for row in checks_by_day
        ],
        "failure_rate_by_day": [
            {
                "day": row.day.isoformat(),
                "failed": row.failed or 0,
                "terminal": row.terminal or 0,
                "rate": (row.failed or 0) / row.terminal if row.terminal else None,
            }
            for row in failure_rows
        ],
        "pending_review_rulesets": session.scalar(
            select(func.count(RuleSet.id)).where(RuleSet.status == "review")
        )
        or 0,
        "rulesets_by_status": {row[0]: row[1] for row in ruleset_rows},
        "guideline_count": session.scalar(select(func.count(Guideline.id))) or 0,
        "rule_count": session.scalar(select(func.count(Rule.id))) or 0,
        "common_violations": [dict(row._mapping) for row in common_violations],
        "violations_by_org_unit": [dict(row._mapping) for row in violations_by_org],
    }
