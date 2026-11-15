import polars as pl
import pytest

from blink_data_lint.core.analyzer import analyze
from blink_data_lint.core.comparison import compare
from blink_data_lint.core.detectors import broken_type, isolation_forest, numeric_outliers
from blink_data_lint.core.roles import code_like


@pytest.mark.parametrize("name", ["id", "VendorID", "PULocationID", "RatecodeID", "payment_type",
                                 "customer_id", "codigo_postal", "Código", "order-status", "tipo_pago"])
def test_explicit_code_name_hints(name):
    assert code_like(name)


@pytest.mark.parametrize("name", ["grid", "fluid", "valid", "amount", "quantity", "passenger_count", "trip_distance"])
def test_names_that_are_not_code_hints(name):
    assert not code_like(name)


def test_rare_legal_code_is_not_a_numeric_outlier():
    series = pl.Series("RatecodeID", [1] * 100 + [2] * 100 + [99])
    result = numeric_outliers(series)
    assert result.count is None and "etiquetas" in result.reason
    measured = numeric_outliers(series.rename("amount"))
    assert measured.count == 1


def test_ml_excludes_codes_but_keeps_measurements():
    frame = pl.DataFrame({"VendorID": [1, 2] * 100, "payment_type": [1, 2] * 100,
                          "amount": list(range(200))})
    result = isolation_forest(frame, enabled=True)
    assert result.columns == ("amount",)
    assert result.evaluated_rows == 200


def test_numeric_looking_identifier_text_is_not_a_broken_type():
    series = pl.Series("customer_id", [str(i) for i in range(99)] + ["unknown"])
    result = broken_type(series)
    assert result.count is None and "Posible ID" in result.reason
    assert broken_type(series.rename("amount")).count == 1


def test_drift_treats_codes_as_categories_without_means():
    previous = pl.DataFrame({"VendorID": [1] * 80 + [2] * 20})
    current = pl.DataFrame({"VendorID": [1] * 20 + [2] * 80})
    result = compare(current, previous)
    methods = {metric.method: metric for metric in result.drift}
    assert set(methods) == {"missing_rate", "total_variation"}
    assert methods["total_variation"].score == pytest.approx(0.6)
    assert methods["total_variation"].flagged


def test_large_integer_codes_stay_exact_categories():
    previous = pl.DataFrame({"customer_id": [2**53 + 1] * 100})
    current = pl.DataFrame({"customer_id": [2**53 + 2] * 100})
    metric = compare(current, previous).drift[-1]
    assert metric.method == "total_variation" and metric.score == 1


def test_name_hint_is_information_not_ci_warning():
    report = analyze(pl.DataFrame({"payment_type": [1, 2] * 100, "amount": range(200)}))
    assert not any(item.severity == "warning" for item in report.findings)
    assert any(item.code == "possible_code" and item.severity == "info" for item in report.findings)
