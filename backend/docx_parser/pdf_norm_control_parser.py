from __future__ import annotations
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, Field

POINTS_TO_MM = 25.4 / 72.0
BODY_FONT_SIZE_PT = 14.0
EXPECTED_LINE_LEADING_RATIO = 1.5
PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")

FindingStatus = Literal["pass", "fail", "not_assessed"]
Confidence = Literal["high", "medium", "low"]
PdfProfileId = Literal["mpgu", "urgpu", "urfu"]


class PdfRuleFinding(BaseModel):
    """Результат проверки одного требования оформления."""

    rule_id: str
    status: FindingStatus
    expected: str
    observed: str
    confidence: Confidence


class PdfMarginsMm(BaseModel):
    """Оценка границ текстовой области в миллиметрах."""

    left: float
    right: float
    top: float
    bottom: float


class PdfNormControlReport(BaseModel):
    """Сводка измерений PDF и результатов проверок выбранного профиля."""

    filename: str
    profile_id: PdfProfileId
    profile_title: str
    page_count: int
    page_sizes_mm: dict[str, int] = Field(default_factory=dict)
    font_sizes_char_count: dict[str, int] = Field(default_factory=dict)
    font_families_char_count: dict[str, int] = Field(default_factory=dict)
    dominant_font_size_pt: Optional[float] = None
    times_new_roman_char_ratio: Optional[float] = None
    median_line_leading_ratio: Optional[float] = None
    median_first_line_indent_mm: Optional[float] = None
    estimated_margins_mm: Optional[PdfMarginsMm] = None
    page_number_locations: dict[str, int] = Field(default_factory=dict)
    findings: list[PdfRuleFinding] = Field(default_factory=list)


@dataclass(frozen=True)
class _Profile:
    """Набор требований одной методички."""

    title: str
    margins_mm: PdfMarginsMm
    page_number_location: Literal["top_center", "bottom_center"]
    require_a4: bool = False


@dataclass(frozen=True)
class _TextLine:
    """Геометрия извлеченной строки PDF в координатах PDF."""

    text: str
    x0_pt: float
    y0_pt: float
    x1_pt: float
    y1_pt: float
    baseline_pt: float
    font_size_pt: float


PROFILES: dict[PdfProfileId, _Profile] = {
    "mpgu": _Profile(
        title="Московский педагогический государственный университет",
        margins_mm=PdfMarginsMm(left=30.0, right=15.0, top=20.0, bottom=20.0),
        page_number_location="top_center",
    ),
    "urgpu": _Profile(
        title="Уральский государственный педагогический университет",
        margins_mm=PdfMarginsMm(left=30.0, right=20.0, top=25.0, bottom=25.0),
        page_number_location="bottom_center",
    ),
    "urfu": _Profile(
        title="Уральский федеральный университет",
        margins_mm=PdfMarginsMm(left=30.0, right=15.0, top=20.0, bottom=20.0),
        page_number_location="bottom_center",
        require_a4=True,
    ),
}


class PdfNormControlParser:
    """Extract observable PDF layout facts and compare them with one guide profile.

    PDF does not retain Word styles or paragraph semantics. Geometry-related
    results are estimates and are marked with medium confidence.
    """

    def __init__(self, pdf_path: str | Path) -> None:
        """Создает анализатор для существующего PDF-файла."""
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.is_file():
            raise FileNotFoundError(f"PDF-файл не найден: {self.pdf_path}")

    def parse(self, profile_id: PdfProfileId) -> PdfNormControlReport:
        """Извлекает измерения PDF и оценивает их по выбранному профилю."""
        if profile_id not in PROFILES:
            raise ValueError(f"Неизвестный профиль оформления: {profile_id!r}")

        try:
            import pymupdf
        except ImportError as exc:
            raise RuntimeError(
                "Для анализа PDF требуется PyMuPDF: установите пакет pymupdf"
            ) from exc

        profile = PROFILES[profile_id]
        page_sizes: dict[str, int] = {}
        font_sizes: dict[str, int] = {}
        font_families: dict[str, int] = {}
        text_lines_by_page: list[list[_TextLine]] = []
        page_number_locations: dict[str, int] = {}
        page_margin_samples: list[PdfMarginsMm] = []
        total_text_chars = 0
        tnr_chars = 0

        with pymupdf.open(str(self.pdf_path)) as pdf:
            if len(pdf) == 0:
                raise ValueError(f"PDF-документ пуст: {self.pdf_path}")

            for page_index, page in enumerate(pdf):
                width_mm = page.rect.width * POINTS_TO_MM
                height_mm = page.rect.height * POINTS_TO_MM
                size_key = f"{width_mm:.1f}x{height_mm:.1f}"
                page_sizes[size_key] = page_sizes.get(size_key, 0) + 1
                page_lines: list[_TextLine] = []
                raw_blocks = page.get_text("dict").get("blocks", [])

                for block in raw_blocks:
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        spans = [
                            span
                            for span in line.get("spans", [])
                            if str(span.get("text", "")).strip()
                        ]
                        text = "".join(
                            str(span.get("text", "")) for span in spans
                        ).strip()
                        if not text:
                            continue

                        chars = sum(
                            len(str(span.get("text", "")).strip()) for span in spans
                        )
                        total_text_chars += chars
                        for span in spans:
                            span_text = str(span.get("text", "")).strip()
                            span_chars = len(span_text)
                            size = round(float(span.get("size", 0.0)), 1)
                            size_key = f"{size:.1f}"
                            font_sizes[size_key] = (
                                font_sizes.get(size_key, 0) + span_chars
                            )
                            family = _font_family(str(span.get("font", "")))
                            font_families[family] = (
                                font_families.get(family, 0) + span_chars
                            )
                            if family == "Times New Roman":
                                tnr_chars += span_chars

                        bbox = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
                        weighted_size = sum(
                            float(span.get("size", 0.0))
                            * len(str(span.get("text", "")).strip())
                            for span in spans
                        ) / max(1, chars)
                        baseline = sum(
                            float(span.get("origin", (0.0, bbox[3]))[1])
                            * len(str(span.get("text", "")).strip())
                            for span in spans
                        ) / max(1, chars)
                        record = _TextLine(
                            text=text,
                            x0_pt=float(bbox[0]),
                            y0_pt=float(bbox[1]),
                            x1_pt=float(bbox[2]),
                            y1_pt=float(bbox[3]),
                            baseline_pt=baseline,
                            font_size_pt=weighted_size,
                        )
                        page_lines.append(record)

                text_lines_by_page.append(page_lines)
                _collect_page_numbers(
                    page_lines,
                    width_mm,
                    height_mm,
                    page_number_locations,
                )

                if page_index > 0:
                    sample = _estimate_page_margins(page_lines, width_mm, height_mm)
                    if sample is not None:
                        page_margin_samples.append(sample)

        if not total_text_chars:
            raise ValueError(f"В PDF не найден извлекаемый текст: {self.pdf_path}")

        dominant_font_size = _dominant_size(font_sizes)
        line_leading_ratios, first_line_indents_mm = _page_layout_metrics(
            text_lines_by_page, dominant_font_size
        )
        tnr_ratio = tnr_chars / total_text_chars
        median_leading = _median(line_leading_ratios)
        median_indent = _median(first_line_indents_mm)
        estimated_margins = _median_margins(page_margin_samples)
        dominant_page_size = max(page_sizes, key=page_sizes.get)

        report = PdfNormControlReport(
            filename=self.pdf_path.name,
            profile_id=profile_id,
            profile_title=profile.title,
            page_count=len(text_lines_by_page),
            page_sizes_mm=page_sizes,
            font_sizes_char_count=font_sizes,
            font_families_char_count=font_families,
            dominant_font_size_pt=dominant_font_size,
            times_new_roman_char_ratio=round(tnr_ratio, 4),
            median_line_leading_ratio=(
                round(median_leading, 3) if median_leading is not None else None
            ),
            median_first_line_indent_mm=(
                round(median_indent, 2) if median_indent is not None else None
            ),
            estimated_margins_mm=estimated_margins,
            page_number_locations=page_number_locations,
        )
        report.findings = _evaluate_profile(
            profile_id=profile_id,
            profile=profile,
            dominant_page_size=dominant_page_size,
            dominant_font_size=dominant_font_size,
            tnr_ratio=tnr_ratio,
            median_leading=median_leading,
            median_indent=median_indent,
            estimated_margins=estimated_margins,
            page_number_locations=page_number_locations,
            total_pages=len(text_lines_by_page),
        )
        return report


def _font_family(font_name: str) -> str:
    normalized = re.sub(r"^[A-Z]{6}\+", "", font_name)
    compact = re.sub(r"[^a-z0-9]", "", normalized.casefold())
    if "timesnewroman" in compact:
        return "Times New Roman"
    if compact.startswith("timesroman") or compact.startswith("times"):
        return "Times-family (not verified as Times New Roman)"
    return "Other"


def _dominant_size(sizes: dict[str, int]) -> Optional[float]:
    if not sizes:
        return None
    return float(max(sizes, key=sizes.get))


def _page_layout_metrics(
    pages: list[list[_TextLine]], dominant_font_size: Optional[float]
) -> tuple[list[float], list[float]]:
    ratios = []
    indents = []
    if dominant_font_size is None:
        return ratios, indents
    for page_lines in pages:
        lines = sorted(page_lines, key=lambda line: (line.baseline_pt, line.x0_pt))
        for current, following in zip(lines, lines[1:]):
            if PAGE_NUMBER_RE.fullmatch(
                current.text.strip()
            ) or PAGE_NUMBER_RE.fullmatch(following.text.strip()):
                continue
            if (
                abs(current.font_size_pt - dominant_font_size) > 0.5
                or abs(following.font_size_pt - dominant_font_size) > 0.5
            ):
                continue
            leading = (following.baseline_pt - current.baseline_pt) / max(
                1.0, (current.font_size_pt + following.font_size_pt) / 2
            )
            if 0.8 <= leading <= 2.5:
                ratios.append(leading)
            indent_mm = (current.x0_pt - following.x0_pt) * POINTS_TO_MM
            if 4.0 <= indent_mm <= 20.0 and 1.3 <= leading <= 2.2:
                indents.append(indent_mm)
    return ratios, indents


def _collect_page_numbers(
    lines: list[_TextLine],
    width_mm: float,
    height_mm: float,
    locations: dict[str, int],
) -> None:
    for line in lines:
        if not PAGE_NUMBER_RE.fullmatch(line.text.strip()):
            continue
        x_center_mm = ((line.x0_pt + line.x1_pt) / 2) * POINTS_TO_MM
        y_center_mm = ((line.y0_pt + line.y1_pt) / 2) * POINTS_TO_MM
        if abs(x_center_mm - width_mm / 2) > 25.0:
            location = "other"
        elif y_center_mm < 30.0:
            location = "top_center"
        elif height_mm - y_center_mm < 30.0:
            location = "bottom_center"
        else:
            continue
        locations[location] = locations.get(location, 0) + 1


def _estimate_page_margins(
    lines: list[_TextLine], width_mm: float, height_mm: float
) -> Optional[PdfMarginsMm]:
    content_lines = [
        line
        for line in lines
        if len(line.text.strip()) >= 8
        and not PAGE_NUMBER_RE.fullmatch(line.text.strip())
    ]
    if len(content_lines) < 8:
        return None
    return PdfMarginsMm(
        left=min(line.x0_pt for line in content_lines) * POINTS_TO_MM,
        right=width_mm - max(line.x1_pt for line in content_lines) * POINTS_TO_MM,
        top=min(line.y0_pt for line in content_lines) * POINTS_TO_MM,
        bottom=height_mm - max(line.y1_pt for line in content_lines) * POINTS_TO_MM,
    )


def _median(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _median_margins(samples: list[PdfMarginsMm]) -> Optional[PdfMarginsMm]:
    if len(samples) < 3:
        return None
    return PdfMarginsMm(
        left=round(statistics.median(item.left for item in samples), 2),
        right=round(statistics.median(item.right for item in samples), 2),
        top=round(statistics.median(item.top for item in samples), 2),
        bottom=round(statistics.median(item.bottom for item in samples), 2),
    )


def _evaluate_profile(
    *,
    profile_id: PdfProfileId,
    profile: _Profile,
    dominant_page_size: str,
    dominant_font_size: Optional[float],
    tnr_ratio: float,
    median_leading: Optional[float],
    median_indent: Optional[float],
    estimated_margins: Optional[PdfMarginsMm],
    page_number_locations: dict[str, int],
    total_pages: int,
) -> list[PdfRuleFinding]:
    findings: list[PdfRuleFinding] = []
    font_ok = tnr_ratio >= 0.9
    findings.append(
        PdfRuleFinding(
            rule_id="body_font_family",
            status="pass" if font_ok else "fail",
            expected="Times New Roman для основного объёма текста",
            observed=f"Times New Roman: {tnr_ratio:.1%} символов",
            confidence="medium",
        )
    )
    size_ok = dominant_font_size == BODY_FONT_SIZE_PT
    findings.append(
        PdfRuleFinding(
            rule_id="body_font_size",
            status="pass" if size_ok else "fail",
            expected="доминирующий размер 14 pt",
            observed=f"доминирует {dominant_font_size} pt",
            confidence="medium",
        )
    )
    leading_ok = (
        median_leading is not None
        and abs(median_leading - EXPECTED_LINE_LEADING_RATIO) <= 0.25
    )
    findings.append(
        PdfRuleFinding(
            rule_id="line_spacing",
            status=("pass" if leading_ok else "fail")
            if median_leading is not None
            else "not_assessed",
            expected="полуторный интервал (оценочный leading ratio около 1.5)",
            observed=(
                f"медианный leading ratio {median_leading:.3f}"
                if median_leading is not None
                else "недостаточно многострочных текстовых блоков"
            ),
            confidence="medium",
        )
    )
    findings.append(
        _numeric_finding(
            "first_line_indent",
            "1,25 см (12,5 мм)",
            median_indent,
            expected_value=12.5,
            tolerance=2.5,
            unit="мм",
        )
    )

    if estimated_margins is None:
        findings.append(
            PdfRuleFinding(
                rule_id="page_margins",
                status="not_assessed",
                expected=_format_margins(profile.margins_mm),
                observed="недостаточно страниц с текстом для оценки положения текстовой области",
                confidence="low",
            )
        )
    else:
        observed = estimated_margins
        edge_errors = [
            abs(getattr(observed, side) - getattr(profile.margins_mm, side))
            for side in ("left", "right", "top")
        ]
        findings.append(
            PdfRuleFinding(
                rule_id="page_margins",
                status="not_assessed",
                expected=_format_margins(profile.margins_mm),
                observed=f"оценка по границам текста: {_format_margins(observed)}",
                confidence="low",
            )
        )
        findings.append(
            PdfRuleFinding(
                rule_id="page_text_area_position",
                status="pass" if max(edge_errors) <= 3.0 else "fail",
                expected=(
                    f"текстовая область: левый край {profile.margins_mm.left:.1f} мм, "
                    f"правый отступ {profile.margins_mm.right:.1f} мм, "
                    f"верхний отступ {profile.margins_mm.top:.1f} мм"
                ),
                observed=(
                    f"левый {observed.left:.1f}, правый {observed.right:.1f}, "
                    f"верхний {observed.top:.1f} мм"
                ),
                confidence="low",
            )
        )

    if profile.require_a4:
        width_mm, height_mm = (float(part) for part in dominant_page_size.split("x"))
        a4_ok = abs(width_mm - 210.0) <= 1.0 and abs(height_mm - 297.0) <= 1.0
        findings.append(
            PdfRuleFinding(
                rule_id="page_size_a4",
                status="pass" if a4_ok else "fail",
                expected="A4 (210 × 297 мм)",
                observed=f"{width_mm:.1f} × {height_mm:.1f} мм",
                confidence="high",
            )
        )

    number_count = sum(page_number_locations.values())
    if number_count < max(3, total_pages // 3):
        findings.append(
            PdfRuleFinding(
                rule_id="page_number_position",
                status="not_assessed",
                expected=profile.page_number_location,
                observed=f"недостаточно распознанных номеров страниц ({number_count})",
                confidence="low",
            )
        )
    else:
        detected_location = max(page_number_locations, key=page_number_locations.get)
        findings.append(
            PdfRuleFinding(
                rule_id="page_number_position",
                status="pass"
                if detected_location == profile.page_number_location
                else "fail",
                expected=profile.page_number_location,
                observed=detected_location,
                confidence="medium",
            )
        )
    return findings


def _numeric_finding(
    rule_id: str,
    expected: str,
    observed: Optional[float],
    *,
    expected_value: float,
    tolerance: float,
    unit: str,
) -> PdfRuleFinding:
    return PdfRuleFinding(
        rule_id=rule_id,
        status=("pass" if abs(observed - expected_value) <= tolerance else "fail")
        if observed is not None
        else "not_assessed",
        expected=expected,
        observed=f"{observed:.2f} {unit}"
        if observed is not None
        else "не удалось оценить по PDF",
        confidence="low",
    )


def _format_margins(margins: PdfMarginsMm) -> str:
    return (
        f"левое {margins.left:.1f}, правое {margins.right:.1f}, "
        f"верхнее {margins.top:.1f}, нижнее {margins.bottom:.1f} мм"
    )
