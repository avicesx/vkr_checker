"""Цвет текста и заливка ячеек таблиц: профильная проверка оформления.

Проверки включаются только профилем вуза: в штатном наборе правила объявлены
без цвета и заливки, потому что методички расходятся, а в реальных ВКР цвет
чаще всего принадлежит стилю ссылки, листингу или шаблону подписи. Тесты
фиксируют, что цветной текст в прозе ловится, гиперссылки и служебные поля не
считаются нарушением, а черный и автоматический цвет допустимы.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from parser.core.schema import (
    ColorValue,
    ParagraphBlock,
    RunFontPropertiesEffective,
    TableBlock,
    TableCell,
    TableRow,
    TextRun,
)
from rule_engine.rules import load_default_ruleset, load_profile_ruleset

from test_rules_urgpu import (
    _borders,
    _doc,
    _engine,
    _para,
    _rule,
    _table,
)


def _colored_run(
    run_id: str,
    text: str,
    *,
    rgb_hex: str = "000000",
    is_auto: bool = False,
    highlight: str = "",
    is_hyperlink: bool = False,
    in_field: bool = False,
) -> TextRun:
    """Создает ран с заданным цветом, подсветкой и признаками ссылки."""
    return TextRun(
        run_id=run_id,
        text=text,
        visible_text=text,
        start_char=0,
        end_char=len(text),
        is_hyperlink=is_hyperlink,
        hyperlink_target="https://example.org" if is_hyperlink else None,
        in_field=in_field,
        effective_props=RunFontPropertiesEffective(
            font_ascii="Times New Roman",
            font_hansi="Times New Roman",
            font_cs="Times New Roman",
            size_pt=14.0,
            color=ColorValue(rgb_hex=rgb_hex, is_auto=is_auto, source="direct"),
            highlight_color=highlight,  # type: ignore[arg-type]
        ),
    )


def _color_rule() -> Dict[str, Any]:
    """Правило про цвет основного текста."""
    return _rule(
        "font_color_body",
        "font",
        scope="body_text",
        params={
            "color": {
                "allowed_colors": ["auto", "000000"],
                "allow_highlight": False,
            }
        },
    )


def _fill_rule() -> Dict[str, Any]:
    """Правило про заливку ячеек таблицы."""
    return _rule(
        "table_no_fill",
        "table_layout",
        scope="table_cells",
        params={
            "require_all_sides_borders": False,
            "require_full_text_width": False,
            "forbid_color_fill": True,
        },
    )


def _table_with_fill(block_id: str, fill: str) -> TableBlock:
    """Собирает таблицу с заливкой единственной ячейки."""
    paragraph = _para(f"{block_id}_p0", "Значение", container_type="table_cell")
    cell = TableCell(
        cell_id=f"{block_id}_c0",
        row_idx=0,
        col_idx=0,
        blocks=[paragraph],
        text_combined="Значение",
        shading_fill=ColorValue(rgb_hex=fill),
        effective_borders=_borders("single"),
    )
    return TableBlock(
        block_id=block_id,
        section_idx=0,
        page_start_est=2,
        page_end_est=2,
        total_width_mm=160.0,
        table_borders=_borders("single"),
        rows=[TableRow(row_idx=0, cells=[cell])],
    )


def _report(rule: Dict[str, Any], blocks: Sequence[Any]):
    """Прогоняет набор из одного правила по документу."""
    return _engine(rule).check(_doc(blocks=blocks))


def _violations(report, rule_id: str) -> List[Any]:
    """Находки правила."""
    return report.violations


def test_font_color_flags_emphasised_run() -> None:
    """Цветной ран в прозе дает находку с адресом и цветом."""
    paragraph = _para(
        "blk1",
        "Цветной фрагмент",
        runs=[_colored_run("r1", "Цветной фрагмент", rgb_hex="CF8E6D")],
    )
    report = _report(_color_rule(), [paragraph])
    violations = _violations(report, "font_color_body")
    assert len(violations) == 1
    violation = violations[0]
    assert violation.violation_id.startswith("font_color_body:blk1")
    assert violation.location.block_id == "blk1"
    assert violation.found["color"] == "CF8E6D"
    assert violation.expected["color"] == ["auto", "000000"]
    assert violation.location.page_start_est == 2


def test_font_color_allows_black_and_auto() -> None:
    """Черный и автоматический цвет нарушениями не считаются."""
    paragraph = _para(
        "blk1",
        "Черный текст",
        runs=[
            _colored_run("r1", "Черный ", rgb_hex="000000"),
            _colored_run("r2", "авто", is_auto=True),
        ],
    )
    report = _report(_color_rule(), [paragraph])
    assert report.violations == []
    assert report.assessments[0].status == "pass"


def test_font_color_skips_hyperlinks_and_fields() -> None:
    """Синий цвет гиперссылки и поля не является цветным выделением."""
    paragraph = _para(
        "blk1",
        "Ссылка",
        runs=[
            _colored_run("r1", "Ссылка", rgb_hex="0000FF", is_hyperlink=True),
            _colored_run("r2", "поле", rgb_hex="0000FF", in_field=True),
        ],
    )
    report = _report(_color_rule(), [paragraph])
    assert report.violations == []


def test_font_color_flags_highlight() -> None:
    """Подсветка текста запрещена, пока не разрешена параметром."""
    paragraph = _para(
        "blk2",
        "Подсвеченный фрагмент",
        runs=[_colored_run("r1", "Подсвеченный фрагмент", highlight="yellow")],
    )
    report = _report(_color_rule(), [paragraph])
    assert len(report.violations) == 1
    assert report.violations[0].found["highlight_color"] == "yellow"


def test_table_fill_flags_colored_cell() -> None:
    """Цветная заливка ячейки дает находку с адресом таблицы."""
    table = _table_with_fill("tbl_0001", "FF0000")
    report = _report(_fill_rule(), [table])
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.violation_id == "table_no_fill:tbl_0001:fill"
    assert violation.found["cells_with_fill"] == 1
    assert violation.found["first_cell_id"] == "tbl_0001_c0"
    assert violation.expected["fill"] == "none"


def test_table_fill_allows_white_and_auto() -> None:
    """Белая заливка и отсутствие заливки допустимы."""
    report = _report(_fill_rule(), [_table_with_fill("tbl_0001", "FFFFFF")])
    assert report.violations == []
    assert report.assessments[0].status == "pass"


def test_default_ruleset_has_no_color_or_fill_checks() -> None:
    """В штатном наборе нет включенных проверок цвета и заливки."""
    ruleset = load_default_ruleset()
    for rule in ruleset.rules:
        params = getattr(rule, "params", None)
        if params is None:
            continue
        assert getattr(params, "color", None) is None
        assert getattr(params, "forbid_color_fill", False) is False


def test_urgpu_profile_enables_color_and_fill() -> None:
    """Профиль УрГПУ включает цвет текста и запрет заливки."""
    ruleset = load_profile_ruleset("urgpu")
    color_rule = ruleset.get_rule("font_color_body")
    assert color_rule.params.color.allowed_colors == ["auto", "000000"]
    assert color_rule.params.color.allow_highlight is False
    table_rule = ruleset.get_rule("table_layout_borders")
    assert table_rule.params.forbid_color_fill is True
