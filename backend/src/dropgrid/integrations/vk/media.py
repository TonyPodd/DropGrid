"""Validate prepared local media before any VK write."""

import hashlib
from io import BytesIO

from PIL import Image

from dropgrid.db.models import MediaAsset
from dropgrid.integrations.vk.errors import VKInputError
from dropgrid.integrations.vk.photos import load_image
from dropgrid.photos.domain import PhotoError, PhotoPolicy
from dropgrid.photos.images import MediaStorage


def checked_media(asset: MediaAsset, storage: MediaStorage, max_bytes: int) -> bytes:
    try:
        data, mime = load_image(storage.path(asset.storage_key), asset.mime_type, max_bytes)
        if mime != "image/jpeg" or not asset.sha256:
            raise ValueError
        if hashlib.sha256(data).hexdigest() != asset.sha256:
            raise ValueError
        with Image.open(BytesIO(data)) as image:
            if image.format != "JPEG" or image.width * image.height > PhotoPolicy().max_pixels:
                raise ValueError
            if (image.width, image.height) != (asset.width, asset.height):
                raise ValueError
            image.verify()
        return data
    except (OSError, ValueError, PhotoError, Image.DecompressionBombError):
        raise VKInputError(
            "media.asset", "MediaAsset file is missing, invalid or inconsistent"
        ) from None
