from app.place_roles import normalize_route_role
from app.text_metrics import count_words, document_metrics, numeric_year_from_period


def test_count_words_handles_chinese_and_latin_text():
    assert count_words("明朝 route 1636") == 4


def test_document_metrics_estimates_non_pdf_pages():
    metrics = document_metrics("山" * 501)
    assert metrics["word_count"] == 501
    assert metrics["page_count"] == 2
    assert metrics["page_count_estimated"] is True


def test_document_metrics_prefers_actual_pdf_page_count():
    metrics = document_metrics("山" * 501, actual_pages=7)
    assert metrics["page_count"] == 7
    assert metrics["page_count_estimated"] is False


def test_numeric_year_from_period():
    assert numeric_year_from_period("明朝崇禎九年（1636）") == 1636
    assert numeric_year_from_period("明朝") is None
    assert numeric_year_from_period("崇禎九年") is None
    assert numeric_year_from_period("公元前221年") == -221
    assert numeric_year_from_period("公元9年") == 9


def test_legacy_route_roles_are_normalized_conservatively():
    assert normalize_route_role("visited") == "passed"
    assert normalize_route_role("passed_and_mentioned") == "passed_and_mentioned"
    assert normalize_route_role("uncertain") == "mentioned_only"
