"""Shared Google service-account credentials and HTTP transport timeouts."""
from __future__ import annotations

from functools import lru_cache

import httplib2
from google.oauth2.service_account import Credentials
from google_auth_httplib2 import AuthorizedHttp

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Google's client libraries default to waiting forever.  A connection that goes
# quiet without closing looks exactly like a slow one, so without these a
# request holds its worker thread until the OS gives up — minutes, not seconds.
# During an event that is the same thing as a frozen bot, and the fix is to fail
# the call, log it, and let the caller's retry path handle it.
#
# Connect quickly, then allow a slower body: a photo upload or a sheet append
# has real data to move before it is done.
CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0

# (connect, read), the form both gspread and httplib2 expect.
TIMEOUT: tuple[float, float] = (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS)


@lru_cache(maxsize=4)
def get_credentials(credentials_file: str) -> Credentials:
    """Load (and cache) service-account credentials from a JSON key file."""
    return Credentials.from_service_account_file(credentials_file, scopes=SCOPES)


def authorized_http(credentials_file: str) -> AuthorizedHttp:
    """Transport for ``google-api-python-client`` with a real timeout.

    ``discovery.build`` builds a plain ``httplib2.Http(timeout=None)`` when it
    is not handed one, which is where the unbounded Drive uploads came from.
    """
    return AuthorizedHttp(
        get_credentials(credentials_file),
        http=httplib2.Http(timeout=TIMEOUT),
    )
