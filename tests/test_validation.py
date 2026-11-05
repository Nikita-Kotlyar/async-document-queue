from __future__ import annotations

from app.services.validation import status_for_issues, validate_fields


def test_valid_invoice_has_no_issues():
    fields = {
        "doc_number": "INV-2099-0001",
        "doc_date": "2026-11-05",
        "counterparty": "ACME Sample LLC",
        "inn": "0000000000",
        "total_amount": "100.00",
        "lines": [{"qty": "1", "unit": "м", "amount": "100.00"}],
    }
    issues = validate_fields("invoice", fields, 0.95, [])
    assert issues == []
    assert status_for_issues(issues) == "validated"


def test_total_mismatch_and_units():
    fields = {
        "doc_number": "INV-2099-0002",
        "doc_date": "2026-11-05",
        "counterparty": "ACME Sample LLC",
        "inn": "0000000000",
        "total_amount": "999.00",
        "lines": [{"qty": "2", "unit": "км", "amount": "100.00"}],
    }
    issues = validate_fields("invoice", fields, 0.95, [])
    codes = {issue.code for issue in issues}
    assert "total_mismatch" in codes
    assert "unit_km_needs_conversion" in codes
    assert status_for_issues(issues) == "needs_review"


def test_negative_qty_is_error():
    fields = {
        "doc_number": "INV-2099-0003",
        "doc_date": "2026-11-05",
        "counterparty": "ACME Sample LLC",
        "inn": "0000000000",
        "total_amount": "-100.00",
        "lines": [{"qty": "-1", "unit": "м", "amount": "-100.00"}],
    }
    issues = validate_fields("invoice", fields, 0.95, [])
    assert any(issue.code == "negative_qty" and issue.severity == "error" for issue in issues)


def test_quality_act_mismatch():
    fields = {"act_id": "QA-2099-0001", "sku": "CBL-X", "qty_act": "950", "qty_erp": "1000", "verdict": "брак"}
    issues = validate_fields("quality_act", fields, 0.9, [])
    codes = {issue.code for issue in issues}
    assert "qty_mismatch" in codes
    assert "quality_rejected" in codes
