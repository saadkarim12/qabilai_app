"""Unit tests for the WhatsApp QuestionCategory enum."""

from __future__ import annotations

from src.enums.whatsapp_questions import QuestionCategory


def test_question_category_values() -> None:
    assert {e.value for e in QuestionCategory} == {
        "commitment",
        "salary",
        "background_validation",
    }


def test_question_category_constructible_from_value() -> None:
    assert QuestionCategory("background_validation") is QuestionCategory.BACKGROUND_VALIDATION
    assert QuestionCategory("commitment") is QuestionCategory.COMMITMENT
    assert QuestionCategory("salary") is QuestionCategory.SALARY
