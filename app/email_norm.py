"""Normalize email addresses for storage and comparison."""


def normalize_email(value: str) -> str:
    return value.strip().lower()
