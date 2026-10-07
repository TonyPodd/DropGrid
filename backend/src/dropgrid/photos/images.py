"""Bounded image decoding and content-addressed local storage."""

import hashlib
import io
import os
import re
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import imagehash
from PIL import Image, ImageOps, UnidentifiedImageError

from dropgrid.photos.domain import PhotoError, PhotoPolicy


@dataclass(frozen=True)
class NormalizedPhoto:
    data: bytes
    sha256: str
    perceptual_hash: str
    width: int
    height: int


def normalize_image(data: bytes, policy: PhotoPolicy) -> NormalizedPhoto:
    if len(data) > policy.max_input_bytes:
        raise PhotoError("image_too_large")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                if (
                    probe.format not in {"JPEG", "PNG", "WEBP"}
                    or getattr(probe, "n_frames", 1) != 1
                ):
                    raise PhotoError("image_format_rejected")
                if probe.width * probe.height > policy.max_pixels:
                    raise PhotoError("image_dimensions_rejected")
                if not policy.dimensions_allowed(probe.width, probe.height):
                    raise PhotoError("image_dimensions_rejected")
                probe.verify()
            with Image.open(io.BytesIO(data)) as image:
                oriented = ImageOps.exif_transpose(image)
                # Alpha flattened to white, metadata omitted by constructing a fresh RGB image.
                rgba = oriented.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                clean.paste(rgba, mask=rgba.getchannel("A"))
                clean.thumbnail(
                    (policy.max_dimension, policy.max_dimension), Image.Resampling.LANCZOS
                )
                output = io.BytesIO()
                clean.save(
                    output, format="JPEG", quality=policy.jpeg_quality, optimize=True, subsampling=0
                )
                normalized = output.getvalue()
                if len(normalized) > policy.max_output_bytes:
                    raise PhotoError("image_too_large")
                return NormalizedPhoto(
                    normalized,
                    hashlib.sha256(normalized).hexdigest(),
                    str(imagehash.dhash(clean)),
                    clean.width,
                    clean.height,
                )
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        OverflowError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise PhotoError("image_invalid") from None


class MediaStorage(Protocol):
    def write(self, image: NormalizedPhoto) -> str: ...
    def path(self, key: str) -> Path: ...


class LocalMediaStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path(self, key: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{2}/[a-f0-9]{64}\.jpg", key) or key[:2] != key[3:5]:
            raise PhotoError("media_unavailable")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise PhotoError("media_unavailable")
        return path

    def write(self, image: NormalizedPhoto) -> str:
        key = f"{image.sha256[:2]}/{image.sha256}.jpg"
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != image.sha256:
                raise PhotoError("media_unavailable")
            return key
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".photo-")
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(image.data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return key
