"""Day 1 tests for the canonical rubric configuration."""

from pathlib import Path

from scoring import DIMENSIONS, RUBRIC_VERSION


def test_exactly_seven_dimensions_exist():
    assert len(DIMENSIONS) == 7


def test_dimension_identifiers_are_unique():
    identifiers = [dimension.identifier for dimension in DIMENSIONS]
    assert len(identifiers) == len(set(identifiers))


def test_weights_total_one_hundred_percent():
    assert sum(dimension.weight for dimension in DIMENSIONS) == 100


def test_every_weight_is_positive():
    assert all(dimension.weight > 0 for dimension in DIMENSIONS)


def test_rubric_version_is_documented():
    assert RUBRIC_VERSION == "v1.0"


def test_reference_ratings_are_documented():
    specification = Path(__file__).parents[1] / "docs" / "ASSESSMENT_SPEC.md"
    text = specification.read_text(encoding="utf-8")
    for rating in (70, 80, 60, 75, 65, 90, "72.00 / 100"):
        assert str(rating) in text
