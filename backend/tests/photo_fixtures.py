import io
import random

from PIL import Image, ImageDraw

from dropgrid.photos.domain import PhotoCandidate


def image_bytes(
    seed: int = 1,
    size: tuple[int, int] = (1000, 1000),
    format: str = "JPEG",
    exif: Image.Exif | None = None,
) -> bytes:
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    rng = random.Random(seed)
    for x in range(10):
        for y in range(10):
            color = tuple(rng.randrange(256) for _ in range(3))
            draw.rectangle(
                (
                    x * size[0] // 10,
                    y * size[1] // 10,
                    (x + 1) * size[0] // 10,
                    (y + 1) * size[1] // 10,
                ),
                fill=color,
            )
    output = io.BytesIO()
    image.save(output, format=format, **({"exif": exif} if exif else {}))
    return output.getvalue()


def candidate(index: int = 1, **changes: object) -> PhotoCandidate:
    data: dict[str, object] = dict(
        provider="fake",
        provider_asset_id=str(index),
        source_page_url=f"https://pixabay.com/photos/test-{index}/",
        candidate_download_url=f"https://cdn.pixabay.com/photo/{index}.jpg",
        creator_name=f"creator-{index}",
        width=1000,
        height=1000,
        tags=("truck", "highway", "грузовик", "дорога"),
        license_code="test-license",
        license_name="Test license",
        license_url="https://pixabay.com/service/license-summary/",
    )
    data.update(changes)
    return PhotoCandidate.model_validate(data)
