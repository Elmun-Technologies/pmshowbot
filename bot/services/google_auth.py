"""Shared Google service-account credentials."""
from __future__ import annotations

from functools import lru_cache

from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


@lru_cache(maxsize=4)
def get_credentials(credentials_file: str) -> Credentials:
    """Load (and cache) service-account credentials from a JSON key file."""
    return Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
