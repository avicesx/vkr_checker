from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from api.main import create_app
from app.models import Guideline, GuidelineLink, OrgUnit, Rule, RuleSet, RuleType
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture
def admin_client(tmp_path: Path) -> TestClient:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Для API-тестов задайте TEST_DATABASE_URL PostgreSQL")
    with TestClient(
        create_app(
            tmp_path / "store",
            database_url=database_url,
            admin_token="test-admin-token",
        )
    ) as client:
        yield client


def _admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-admin-token"}


def _make_rule_set(client: TestClient, status: str = "review") -> tuple[int, int, int]:
    session_factory = client.app.state.session_factory
    with session_factory.begin() as session:
        guideline = Guideline(title="Тестовая методичка", year=2025)
        session.add(guideline)
        session.flush()
        rule_type = session.scalar(select(RuleType).where(RuleType.code == "font"))
        if rule_type is None:
            rule_type = RuleType(code="font", name="Шрифт")
            session.add(rule_type)
            session.flush()
        ruleset = RuleSet(
            guideline_id=guideline.id,
            name="Тестовый набор",
            version=1,
            status=status,
        )
        session.add(ruleset)
        session.flush()
        rule = Rule(
            ruleset_id=ruleset.id,
            rule_type_id=rule_type.id,
            name="Размер шрифта",
            params={"size_pt": 14},
            scope="body_text",
            quote="Размер шрифта 14 пт",
            quote_location="пункт 1.1",
            confidence=0.8,
            status="review" if status == "review" else "approved",
            origin="llm",
        )
        session.add(rule)
        session.flush()
        return guideline.id, ruleset.id, rule.id


def test_admin_routes_require_token_but_public_routes_do_not(
    admin_client: TestClient,
) -> None:
    response = admin_client.get("/api/v1/admin/org-units")

    assert response.status_code == 401
    assert (
        admin_client.get(
            "/api/v1/admin/org-units", headers=_admin_headers()
        ).status_code
        == 200
    )
    assert (
        admin_client.get(
            "/api/v1/checks/00000000-0000-0000-0000-000000000000"
        ).status_code
        == 404
    )


def test_org_unit_allows_skipped_levels_and_rejects_invalid_parent(
    admin_client: TestClient,
) -> None:
    university = admin_client.post(
        "/api/v1/admin/org-units",
        headers=_admin_headers(),
        json={"kind": "university", "name": "Университет"},
    )
    assert university.status_code == 201

    department = admin_client.post(
        "/api/v1/admin/org-units",
        headers=_admin_headers(),
        json={
            "kind": "department",
            "name": "Кафедра",
            "parent_id": university.json()["id"],
        },
    )
    assert department.status_code == 201

    invalid = admin_client.post(
        "/api/v1/admin/org-units",
        headers=_admin_headers(),
        json={
            "kind": "university",
            "name": "Дочерний университет",
            "parent_id": university.json()["id"],
        },
    )
    assert invalid.status_code == 422


def test_rule_edit_validates_engine_params_and_creates_new_version(
    admin_client: TestClient,
) -> None:
    _, ruleset_id, rule_id = _make_rule_set(admin_client, status="active")

    invalid = admin_client.patch(
        f"/api/v1/admin/rules/{rule_id}",
        headers=_admin_headers(),
        json={"params": {"size_pt": "large"}},
    )
    assert invalid.status_code == 422

    updated = admin_client.patch(
        f"/api/v1/admin/rules/{rule_id}",
        headers=_admin_headers(),
        json={"name": "Размер основного шрифта"},
    )
    assert updated.status_code == 200
    assert updated.json()["ruleset_id"] != ruleset_id

    session_factory = admin_client.app.state.session_factory
    with session_factory() as session:
        original_set = session.get(RuleSet, ruleset_id)
        original_rule = session.get(Rule, rule_id)
        new_set = session.get(RuleSet, updated.json()["ruleset_id"])
        new_rule = session.get(Rule, updated.json()["rule_id"])
        assert original_set.status == "active"
        assert original_rule.name == "Размер шрифта"
        assert new_set.parent_ruleset_id == original_set.id
        assert new_set.version == original_set.version + 1
        assert new_rule.name == "Размер основного шрифта"


def test_activation_archives_previous_ruleset_and_rejects_unreviewed(
    admin_client: TestClient,
) -> None:
    guideline_id, first_id, _ = _make_rule_set(admin_client, status="active")
    second = admin_client.app.state.session_factory()
    try:
        with second.begin():
            second_set = RuleSet(
                guideline_id=guideline_id,
                name="Новый набор",
                version=2,
                status="approved",
            )
            second.add(second_set)
            second.flush()
            rule_type = second.scalar(select(RuleType).where(RuleType.code == "font"))
            second.add(
                Rule(
                    ruleset_id=second_set.id,
                    rule_type_id=rule_type.id,
                    name="Шрифт",
                    params={"size_pt": 14},
                    scope="body_text",
                    status="approved",
                )
            )
            second_id = second_set.id
    finally:
        second.close()

    response = admin_client.post(
        f"/api/v1/admin/rulesets/{second_id}/activate",
        headers=_admin_headers(),
    )
    assert response.status_code == 200

    session_factory = admin_client.app.state.session_factory
    with session_factory() as session:
        assert session.get(RuleSet, first_id).status == "archived"
        assert session.get(RuleSet, second_id).status == "active"


def test_review_decisions_allow_rejected_rules_but_require_an_approved_rule(
    admin_client: TestClient,
) -> None:
    _, ruleset_id, rule_id = _make_rule_set(admin_client)
    decision = admin_client.post(
        f"/api/v1/admin/rules/{rule_id}/decision",
        headers=_admin_headers(),
        json={"decision": "rejected"},
    )

    assert decision.status_code == 200
    assert decision.json()["ruleset_status"] == "rejected"
    activation = admin_client.post(
        f"/api/v1/admin/rulesets/{ruleset_id}/activate",
        headers=_admin_headers(),
    )
    assert activation.status_code == 409


def test_bulk_approval_skips_unknown_confidence_and_allows_rejections(
    admin_client: TestClient,
) -> None:
    _, ruleset_id, _ = _make_rule_set(admin_client)
    session_factory = admin_client.app.state.session_factory
    with session_factory.begin() as session:
        rule_type = session.scalar(select(RuleType).where(RuleType.code == "font"))
        session.add(
            Rule(
                ruleset_id=ruleset_id,
                rule_type_id=rule_type.id,
                name="Правило без уверенности",
                params={"size_pt": 14},
                scope="body_text",
                confidence=None,
                status="review",
            )
        )

    bulk = admin_client.post(
        f"/api/v1/admin/rulesets/{ruleset_id}/approve-by-confidence",
        headers=_admin_headers(),
        json={"confidence_threshold": 0.7},
    )
    assert bulk.status_code == 200
    assert bulk.json()["approved_count"] == 1
    assert bulk.json()["ruleset_status"] == "review"

    session_factory = admin_client.app.state.session_factory
    with session_factory() as session:
        unknown_confidence_rule = session.scalar(
            select(Rule).where(
                Rule.ruleset_id == ruleset_id,
                Rule.confidence.is_(None),
            )
        )
        unknown_confidence_rule_id = unknown_confidence_rule.id

    rejected = admin_client.post(
        f"/api/v1/admin/rules/{unknown_confidence_rule_id}/decision",
        headers=_admin_headers(),
        json={"decision": "rejected"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["ruleset_status"] == "approved"

    activated = admin_client.post(
        f"/api/v1/admin/rulesets/{ruleset_id}/activate",
        headers=_admin_headers(),
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"


def test_approved_exact_quote_is_linked_when_reused(admin_client: TestClient) -> None:
    _, source_set_id, source_rule_id = _make_rule_set(admin_client, status="active")
    session_factory = admin_client.app.state.session_factory
    with session_factory.begin() as session:
        source_set = session.get(RuleSet, source_set_id)
        new_set = RuleSet(
            guideline_id=source_set.guideline_id,
            name="Повторное правило",
            version=2,
            status="review",
        )
        session.add(new_set)
        session.flush()
        source_rule = session.get(Rule, source_rule_id)
        repeated_rule = Rule(
            ruleset_id=new_set.id,
            rule_type_id=source_rule.rule_type_id,
            name=source_rule.name,
            params=source_rule.params,
            scope=source_rule.scope,
            quote="  Размер   шрифта 14 пт ",
            confidence=0.9,
            status="review",
        )
        session.add(repeated_rule)
        session.flush()
        new_set_id = new_set.id
        repeated_rule_id = repeated_rule.id

    response = admin_client.post(
        f"/api/v1/admin/rules/{repeated_rule_id}/decision",
        headers=_admin_headers(),
        json={"decision": "approved"},
    )
    assert response.status_code == 200
    with session_factory() as session:
        repeated = session.get(Rule, repeated_rule_id)
        reused = session.get(Rule, repeated.inherited_from_rule_id)
        assert reused is not None
        assert reused.id != repeated.id
        assert reused.rule_type_id == repeated.rule_type_id
        assert reused.params == repeated.params
        assert reused.scope == repeated.scope
        assert reused.status == "approved"
        assert session.get(RuleSet, new_set_id).status == "approved"


def test_guideline_pagination_counts_each_organization_link(
    admin_client: TestClient,
) -> None:
    session_factory = admin_client.app.state.session_factory
    unique_id = uuid4().hex
    with session_factory.begin() as session:
        university = OrgUnit(
            kind="university", name=f"Pagination university {unique_id}"
        )
        institute = OrgUnit(kind="institute", name=f"Pagination institute {unique_id}")
        guideline = Guideline(
            title=f"Методичка с несколькими привязками {unique_id}",
            year=2026,
        )
        session.add_all([university, institute, guideline])
        session.flush()
        session.add_all(
            [
                GuidelineLink(
                    guideline_id=guideline.id,
                    org_unit_id=university.id,
                ),
                GuidelineLink(
                    guideline_id=guideline.id,
                    org_unit_id=institute.id,
                    program_code="01",
                ),
            ]
        )
        guideline_title = guideline.title
        expected_org_unit_ids = {university.id, institute.id}

    headers = _admin_headers()
    first_page = admin_client.get(
        "/api/v1/admin/guidelines",
        headers=headers,
        params={"search": guideline_title, "page": 1, "page_size": 1},
    )
    second_page = admin_client.get(
        "/api/v1/admin/guidelines",
        headers=headers,
        params={"search": guideline_title, "page": 2, "page_size": 1},
    )

    assert first_page.status_code == second_page.status_code == 200
    assert first_page.json()["total"] == 2
    assert first_page.json()["pages"] == 2
    assert second_page.json()["total"] == 2
    assert {
        first_page.json()["items"][0]["org_unit_id"],
        second_page.json()["items"][0]["org_unit_id"],
    } == expected_org_unit_ids
