"""Upload car photos to Google Drive and return image URLs for the sheet.

All Google API calls here are synchronous (google-api-python-client); callers
should wrap the async helpers, which already run the work in a thread.
"""
from __future__ import annotations

import asyncio
import logging
import os

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .google_auth import get_credentials

logger = logging.getLogger(__name__)

# A direct, embeddable image URL that Google Sheets' =IMAGE() can render.
_IMAGE_URL = "https://drive.google.com/uc?export=view&id={file_id}"


def _upload_one(service, folder_id: str, path: str, name: str) -> str:
    metadata = {"name": name, "parents": [folder_id]}
    media = MediaFileUpload(path, resumable=False)
    created = (
        service.files()
        .create(body=metadata, media_body=media, fields="id", supportsAllDrives=True)
        .execute()
    )
    file_id = created["id"]
    # Make the file readable by anyone with the link so =IMAGE() can load it.
    service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
        supportsAllDrives=True,
    ).execute()
    return _IMAGE_URL.format(file_id=file_id)


def _upload_photos(credentials_file: str, folder_id: str, files: list[tuple[str, str]]) -> list[str]:
    """files: list of (local_path, drive_name). Returns list of image URLs."""
    service = build("drive", "v3", credentials=get_credentials(credentials_file), cache_discovery=False)
    urls: list[str] = []
    for path, name in files:
        if not os.path.exists(path):
            logger.warning("Photo not found for upload: %s", path)
            urls.append("")
            continue
        urls.append(_upload_one(service, folder_id, path, name))
    return urls


async def upload_photos(
    credentials_file: str,
    folder_id: str,
    files: list[tuple[str, str]],
) -> list[str]:
    """Async wrapper. Returns a list of embeddable image URLs (aligned to input)."""
    return await asyncio.to_thread(_upload_photos, credentials_file, folder_id, files)
