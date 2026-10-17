"""Tests for Day 2 intake validation and normalization."""

from intake import split_lines, validate_submission


def test_required_fields_and_description_are_validated():
    errors = validate_submission({})
    assert "Startup name is required." in errors
    assert "Short startup description should be at least 20 characters." in errors


def test_valid_minimum_submission_has_no_errors():
    data = {
        "startup_name": "GreenLoop",
        "startup_description": "Reusable packaging tracking for independent retailers.",
        "problem_statement": "Retailers struggle to manage reusable packaging.",
        "proposed_solution": "A simple return and tracking service.",
        "target_customers": "Independent food retailers",
    }
    assert validate_submission(data) == []


def test_split_lines_removes_empty_lines_and_whitespace():
    assert split_lines(" one\n\n two \n") == ["one", "two"]
