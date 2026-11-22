"""Справочник видов проверок: каталог кодов и начальное наполнение `rule_type`.

Код вида проверки (`rule_type.code`) — машинный ключ, по которому движок ищет
функцию проверки. Он же используется как discriminator в схеме набора правил,
поэтому каталог обязан совпадать с `rule_engine.rules.CODE_ALLOWED_SCOPES`.
Расхождение ловится тестом `tests/test_rules_from_db.py`.

Каталог — единственный источник названий и области по умолчанию: админка берёт
отсюда `DEFAULT_RULE_SCOPES`, а `seed_rule_types` наполняет одноимённую таблицу
при инициализации базы. Сид идемпотентен: повторный запуск не создаёт дублей и
возвращает названия к каталогу, если их правили вручную.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import RuleType


@dataclass(frozen=True)
class RuleTypeSpec:
    """Описание вида проверки для справочника."""

    code: str
    name: str
    default_scope: str
    description: str


RULE_TYPE_CATALOGUE: tuple[RuleTypeSpec, ...] = (
    RuleTypeSpec(
        "font",
        "Шрифт текста",
        "body_text",
        "Гарнитура и кегль абзацев основного текста и ячеек таблиц.",
    ),
    RuleTypeSpec(
        "indent",
        "Отступ первой строки",
        "body_text",
        "Абзацный отступ первой строки основного текста.",
    ),
    RuleTypeSpec(
        "margin",
        "Поля страницы",
        "sections",
        "Размеры полей страницы в миллиметрах.",
    ),
    RuleTypeSpec(
        "heading_levels",
        "Уровни заголовков",
        "headings",
        "Набор допустимых уровней заголовков и их порядок.",
    ),
    RuleTypeSpec(
        "heading_numbering",
        "Нумерация заголовков",
        "headings",
        "Схема нумерации заголовков разделов и подразделов.",
    ),
    RuleTypeSpec(
        "heading_format",
        "Оформление заголовка",
        "headings",
        "Шрифт, регистр, точка в конце и выравнивание заголовков.",
    ),
    RuleTypeSpec(
        "required_sections",
        "Обязательные разделы",
        "document_structure",
        "Наличие обязательных структурных элементов работы.",
    ),
    RuleTypeSpec(
        "toc",
        "Оглавление",
        "toc",
        "Наличие и оформление оглавления.",
    ),
    RuleTypeSpec(
        "page_number_position",
        "Номера страниц",
        "headers_footers",
        "Расположение нумерации страниц в колонтитуле.",
    ),
    RuleTypeSpec(
        "page_format",
        "Формат страницы",
        "sections",
        "Размер и ориентация страницы.",
    ),
    RuleTypeSpec(
        "line_spacing",
        "Межстрочный интервал",
        "body_text",
        "Межстрочный интервал основного текста.",
    ),
    RuleTypeSpec(
        "bibliography_link",
        "Ссылки на источники",
        "bibliography",
        "Каждая запись списка литературы имеет ссылку в тексте.",
    ),
    RuleTypeSpec(
        "bibliography_cited",
        "Цитирование источников",
        "bibliography",
        "Каждый источник списка литературы процитирован в тексте.",
    ),
    RuleTypeSpec(
        "bibliography_format",
        "Оформление записи литературы",
        "bibliography",
        "Оформление библиографической записи по типу источника.",
    ),
    RuleTypeSpec(
        "caption_numbering",
        "Нумерация подписей",
        "captions",
        "Сквозная или раздельная нумерация подписей рисунков и таблиц.",
    ),
    RuleTypeSpec(
        "caption_format",
        "Оформление подписи",
        "captions",
        "Формат подписи: метка, разделитель, точка в конце.",
    ),
    RuleTypeSpec(
        "caption_reference",
        "Ссылки на объекты",
        "captions",
        "На каждый рисунок и таблицу есть ссылка в тексте.",
    ),
    RuleTypeSpec(
        "alignment",
        "Выравнивание",
        "captions",
        "Выравнивание абзацев, подписей и ячеек таблиц.",
    ),
    RuleTypeSpec(
        "widow_control",
        "Висячие строки",
        "body_text",
        "Запрет одиночных строк абзаца на границе страниц.",
    ),
    RuleTypeSpec(
        "keep_next",
        "Заголовок не отрывается от текста",
        "headings",
        "Заголовок удерживается на странице вместе со следующим абзацем.",
    ),
    RuleTypeSpec(
        "heading_page_break",
        "Заголовок с новой страницы",
        "headings",
        "Крупные разделы начинаются с новой страницы.",
    ),
    RuleTypeSpec(
        "document_volume",
        "Объём работы",
        "document",
        "Объём работы в страницах и словах.",
    ),
    RuleTypeSpec(
        "microtypography",
        "Типографика текста",
        "body_paragraphs",
        "Кавычки, тире, лишние пробелы и неразрывные пробелы.",
    ),
    RuleTypeSpec(
        "equation_numbering",
        "Нумерация формул",
        "document",
        "Нумерация формул и наличие пояснения «где».",
    ),
    RuleTypeSpec(
        "bibliography_count",
        "Объём списка литературы",
        "bibliography",
        "Минимальное и максимальное число источников.",
    ),
    RuleTypeSpec(
        "bibliography_order",
        "Порядок списка литературы",
        "bibliography",
        "Сквозная нумерация записей списка литературы.",
    ),
    RuleTypeSpec(
        "bibliography_recency",
        "Свежесть источников",
        "bibliography",
        "Доля источников, изданных за последние годы.",
    ),
)
"""Каталог видов проверок, поддерживаемых движком."""

DEFAULT_SCOPE_BY_CODE: dict[str, str] = {
    spec.code: spec.default_scope for spec in RULE_TYPE_CATALOGUE
}
"""Область проверки по умолчанию для каждого кода."""


def _session_from(bind: Union[Engine, Session]) -> tuple[Session, bool]:
    """Возвращает сессию и признак, что её нужно закрыть самому."""
    if isinstance(bind, Session):
        return bind, False
    return Session(bind), True


def seed_rule_types(bind: Union[Engine, Session]) -> int:
    """Наполняет справочник видов проверок и возвращает число созданных строк.

    Работает идемпотентно: существующие коды не дублируются, а названия и
    описания возвращаются к каталогу, если их правили вручную. Удаление строк
    не выполняется: правило может ссылаться на тип, поэтому лишние коды
    остаются как есть.
    """
    session, should_close = _session_from(bind)
    try:
        existing = {
            rule_type.code: rule_type
            for rule_type in session.scalars(select(RuleType)).all()
        }
        created = 0
        for spec in RULE_TYPE_CATALOGUE:
            rule_type = existing.get(spec.code)
            if rule_type is None:
                session.add(
                    RuleType(
                        code=spec.code,
                        name=spec.name,
                        description=spec.description,
                    )
                )
                created += 1
                continue
            if rule_type.name != spec.name:
                rule_type.name = spec.name
            if rule_type.description != spec.description:
                rule_type.description = spec.description
        session.flush()
        if should_close:
            session.commit()
        return created
    finally:
        if should_close:
            session.close()
