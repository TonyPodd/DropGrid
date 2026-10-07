import io
from dataclasses import replace

import pytest
from photo_fixtures import image_bytes
from PIL import Image

from dropgrid.photos.domain import Deduplicator, PhotoError, PhotoPolicy
from dropgrid.photos.images import LocalMediaStorage, normalize_image


@pytest.mark.parametrize("format", ["JPEG", "PNG", "WEBP"])
def test_normalization_formats(format):
    source = image_bytes(format=format)
    image = normalize_image(source, PhotoPolicy())
    assert image.data.startswith(b"\xff\xd8")
    assert image.width == image.height == 1000
    assert normalize_image(source, PhotoPolicy()).sha256 == image.sha256
    with Image.open(io.BytesIO(image.data)) as result:
        assert result.mode == "RGB" and not result.getexif() and not result.info.get("exif")


def test_orientation_and_metadata_stripping():
    exif = Image.Exif()
    exif[274], exif[270], exif[34853] = 6, "private description", {1: "N", 2: (1, 2, 3)}
    image = normalize_image(image_bytes(size=(1200, 900), exif=exif), PhotoPolicy())
    assert (image.width, image.height) == (900, 1200)
    with Image.open(io.BytesIO(image.data)) as result:
        assert not result.getexif()


def test_exact_near_and_distinct():
    policy = PhotoPolicy()
    a = normalize_image(image_bytes(seed=1), policy)
    b = normalize_image(image_bytes(seed=1, format="PNG"), policy)
    c = normalize_image(image_bytes(seed=4), policy)
    assert a.sha256 == normalize_image(image_bytes(seed=1), policy).sha256
    assert Deduplicator().near(a.perceptual_hash, b.perceptual_hash)
    assert not Deduplicator().near(a.perceptual_hash, c.perceptual_hash)


@pytest.mark.parametrize(
    "payload", [b"<html>not an image</html>", b"<svg/>", b"garbage", b"\xff\xd8broken"]
)
def test_invalid_images(payload):
    with pytest.raises(PhotoError):
        normalize_image(payload, PhotoPolicy())


def test_bounds_and_no_upscale(monkeypatch):
    policy = PhotoPolicy()
    with pytest.raises(PhotoError):
        normalize_image(image_bytes(size=(100, 100)), policy)
    with pytest.raises(PhotoError):
        normalize_image(image_bytes(size=(4000, 900)), policy)
    with pytest.raises(PhotoError):
        normalize_image(image_bytes(), replace(policy, max_input_bytes=100))
    with pytest.raises(PhotoError):
        normalize_image(image_bytes(), replace(policy, max_output_bytes=100))
    with pytest.raises(PhotoError):
        normalize_image(image_bytes(), replace(policy, max_pixels=1000))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(PhotoError):
        normalize_image(image_bytes(), policy)


def test_storage_atomic_idempotent_and_traversal(tmp_path):
    storage = LocalMediaStorage(tmp_path)
    image = normalize_image(image_bytes(), PhotoPolicy())
    key = storage.write(image)
    assert storage.write(image) == key
    assert storage.path(key).read_bytes() == image.data
    assert not list(tmp_path.rglob(".photo-*"))
    for bad in ("../secret", "/etc/passwd", "aa/" + image.sha256 + ".jpg"):
        with pytest.raises(PhotoError):
            storage.path(bad)
    outside = tmp_path.parent / "outside-photo"
    outside.mkdir(exist_ok=True)
    root = tmp_path / "symlink-test"
    root.mkdir()
    (root / image.sha256[:2]).symlink_to(outside, target_is_directory=True)
    with pytest.raises(PhotoError):
        LocalMediaStorage(root).path(key)
