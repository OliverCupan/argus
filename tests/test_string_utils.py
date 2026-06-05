"""Tests for string utility functions."""

import pytest

from src.utils.string_utils import truncate


@pytest.mark.unit
class TestTruncate:
    """Test suite for the truncate function."""

    # Normal cases
    def test_truncate_basic(self) -> None:
        """Test basic truncation with default suffix."""
        result = truncate("Hello World", 8, "…")
        assert result == "Hello W…"

    def test_truncate_with_custom_suffix(self) -> None:
        """Test truncation with custom suffix."""
        result = truncate("Hello World", 8, "...")
        assert result == "Hello..."

    def test_truncate_no_truncation_needed(self) -> None:
        """Test that text shorter than max_len is returned unchanged."""
        result = truncate("Hello", 10, "…")
        assert result == "Hello"

    # Edge cases
    def test_truncate_empty_string(self) -> None:
        """Test truncation of an empty string."""
        result = truncate("", 5, "…")
        assert result == ""

    def test_truncate_exact_length(self) -> None:
        """Test when text length equals max_len."""
        result = truncate("Hello", 5, "…")
        assert result == "Hello"

    def test_truncate_single_character(self) -> None:
        """Test truncation to minimal length."""
        result = truncate("Hello World", 2, "…")
        assert result == "H…"

    def test_truncate_empty_suffix(self) -> None:
        """Test truncation with empty suffix."""
        result = truncate("Hello World", 5, "")
        assert result == "Hello"

    def test_truncate_unicode_characters(self) -> None:
        """Test truncation with unicode/emoji characters."""
        result = truncate("Hello😊World", 8, "…")
        assert result == "Hello😊W…"

    # Error cases
    def test_truncate_negative_max_len(self) -> None:
        """Test that negative max_len raises ValueError."""
        with pytest.raises(ValueError, match="max_len must be positive"):
            truncate("Hello", -5, "…")

    def test_truncate_zero_max_len(self) -> None:
        """Test that zero max_len raises ValueError."""
        with pytest.raises(ValueError, match="max_len must be positive"):
            truncate("Hello", 0, "…")

    def test_truncate_suffix_longer_than_max_len(self) -> None:
        """Test that suffix longer than max_len raises ValueError."""
        with pytest.raises(ValueError, match="suffix length .* must be less than"):
            truncate("Hello", 3, "...")

    def test_truncate_suffix_equals_max_len(self) -> None:
        """Test that suffix equal to max_len raises ValueError."""
        with pytest.raises(ValueError, match="suffix length .* must be less than"):
            truncate("Hello", 3, "...")

    def test_truncate_non_string_text(self) -> None:
        """Test that non-string text raises TypeError."""
        with pytest.raises(TypeError, match="text must be a string"):
            truncate(123, 5, "…")  # type: ignore

    def test_truncate_non_string_suffix(self) -> None:
        """Test that non-string suffix raises TypeError."""
        with pytest.raises(TypeError, match="suffix must be a string"):
            truncate("Hello", 5, 123)  # type: ignore

    def test_truncate_none_text(self) -> None:
        """Test that None text raises TypeError."""
        with pytest.raises(TypeError, match="text must be a string"):
            truncate(None, 5, "…")  # type: ignore

    def test_truncate_none_suffix(self) -> None:
        """Test that None suffix raises TypeError."""
        with pytest.raises(TypeError, match="suffix must be a string"):
            truncate("Hello", 5, None)  # type: ignore
