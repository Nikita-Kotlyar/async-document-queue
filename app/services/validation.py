from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.extraction import parse_decimal

ALLOWED_UNITS = {"м", "км", "шт", "барабан", "упаковка", "палетоместо", "рулон", "смеш."}


@dataclass
class IssueData:
    code: str
    severity: str
    message: str
    field: str | None = None


def _issue(code: str, severity: str, message: str, field: str | None = None) -> IssueData:
    return IssueData(code=code, severity=severity, message=message, field=field)


def validate_fields(
    doc_type: str,
    fields: dict,
    confidence: float,
    extraction_warnings: list[str] | None = None,
) -> list[IssueData]:
    issues: list[IssueData] = []

    for warning in extraction_warnings or []:
        code = warning.split(":", 1)[0] or "extraction_warning"
        issues.append(_issue(code, "warning", warning))

    if confidence < 0.45:
        issues.append(_issue("low_ocr_confidence", "warning", f"OCR confidence is {confidence:.2f}"))

    if doc_type == "quality_act":
        for key in ("act_id", "sku", "qty_act"):
            if not fields.get(key):
                issues.append(_issue(f"missing_{key}", "warning", f"Required field is missing: {key}", key))
        qty_act = parse_decimal(fields.get("qty_act"))
        qty_erp = parse_decimal(fields.get("qty_erp"))
        if qty_act is not None and qty_erp is not None and qty_act != qty_erp:
            issues.append(
                _issue(
                    "qty_mismatch",
                    "warning",
                    f"Act quantity {qty_act} differs from ERP quantity {qty_erp}",
                    "qty_act",
                )
            )
        if str(fields.get("verdict", "")).lower().startswith("брак"):
            issues.append(_issue("quality_rejected", "warning", "Quality act verdict is 'брак'", "verdict"))
        return _dedupe(issues)

    for key in ("doc_number", "doc_date", "counterparty"):
        if not fields.get(key):
            issues.append(_issue(f"missing_{key}", "warning", f"Required field is missing: {key}", key))

    inn = fields.get("inn")
    if inn:
        if len(str(inn)) not in (10, 12):
            issues.append(_issue("invalid_inn", "error", f"INN has invalid length: {inn}", "inn"))
    else:
        issues.append(_issue("missing_inn", "warning", "Supplier INN is missing", "inn"))

    lines = fields.get("lines") or []
    if not lines:
        issues.append(_issue("no_line_items", "warning", "No invoice line items were extracted", "lines"))

    total = parse_decimal(fields.get("total_amount"))
    line_total = sum((parse_decimal(line.get("amount")) or Decimal("0") for line in lines), Decimal("0"))
    if lines and total is not None and line_total > 0:
        if abs(total - line_total) > Decimal("0.01"):
            issues.append(
                _issue(
                    "total_mismatch",
                    "warning",
                    f"Invoice total {total} differs from line sum {line_total}",
                    "total_amount",
                )
            )

    for index, line in enumerate(lines):
        qty = parse_decimal(line.get("qty"))
        unit = line.get("unit")
        if qty is not None and qty < 0:
            issues.append(_issue("negative_qty", "error", f"Line {index + 1} has negative quantity", "qty"))
        if not unit:
            issues.append(_issue("missing_unit", "warning", f"Line {index + 1} has no unit", "unit"))
        elif unit not in ALLOWED_UNITS:
            issues.append(
                _issue("unit_mismatch", "warning", f"Line {index + 1} uses unsupported unit '{unit}'", "unit")
            )
        elif unit == "км":
            issues.append(
                _issue(
                    "unit_km_needs_conversion",
                    "warning",
                    f"Line {index + 1} is in km and requires conversion to meters",
                    "unit",
                )
            )

    return _dedupe(issues)


def _dedupe(issues: list[IssueData]) -> list[IssueData]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[IssueData] = []
    for issue in issues:
        key = (issue.code, issue.message, issue.field)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def status_for_issues(issues: list[IssueData]) -> str:
    if any(issue.severity == "error" for issue in issues):
        return "needs_review"
    if issues:
        return "needs_review"
    return "validated"
