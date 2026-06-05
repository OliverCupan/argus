"""String utility functions."""


def truncate(text: str, max_len: int, suffix: str = "…") -> str:
    """
    Truncate a string to a maximum length, optionally appending a suffix.

    This function truncates text by cutting at character boundaries (does not
    respect word boundaries). If the text is already shorter than or equal to
    max_len, it is returned unchanged without the suffix appended.

    Args:
        text: The string to truncate.
        max_len: Maximum length of the returned string (including suffix).
                 Must be a positive integer.
        suffix: String to append when truncation occurs. Defaults to "…".
                If len(suffix) >= max_len, a ValueError is raised.

    Returns:
        The truncated string with suffix appended if truncation occurred,
        or the original text if no truncation was needed.

    Raises:
        ValueError: If max_len is not positive or if len(suffix) >= max_len.
        TypeError: If text or suffix is not a string.

    Examples:
        >>> truncate("Hello World", 8, "…")
        'Hello W…'
        >>> truncate("Hello", 10, "…")
        'Hello'
        >>> truncate("", 5, "…")
        ''
    """
    # Type validation
    if not isinstance(text, str):
        raise TypeError(f"text must be a string, got {type(text).__name__}")
    if not isinstance(suffix, str):
        raise TypeError(f"suffix must be a string, got {type(suffix).__name__}")

    # Range validation
    if max_len <= 0:
        raise ValueError(f"max_len must be positive, got {max_len}")
    if len(suffix) >= max_len:
        raise ValueError(
            f"suffix length ({len(suffix)}) must be less than max_len ({max_len})"
        )

    # No truncation needed
    if len(text) <= max_len:
        return text

    # Truncate and append suffix
    available_len = max_len - len(suffix)
    return text[:available_len] + suffix
