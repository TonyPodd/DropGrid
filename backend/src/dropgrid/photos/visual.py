"""Library-neutral, versioned normalized float32 embeddings and deterministic scoring."""

import asyncio
import hashlib
import importlib
import io
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from dropgrid.photos.domain import (
    PhotoCandidate,
    PhotoError,
    PhotoQueryPlan,
    PhotoRanker,
    PhotoSearch,
    normalize_category,
)
from dropgrid.photos.timings import timed

MODEL_REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
MODEL = f"Xenova/clip-vit-base-patch32:vision-int8@{MODEL_REVISION}:preprocess-v1"
MODEL_SHA256 = "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"
VISUAL_WEIGHT = 0.60
METADATA_WEIGHT = 0.30
QUALITY_WEIGHT = 0.10
TOP_K = 5
BASE_SCORE_SCALE = 15.0
QUALITY_SHORT_SIDE_SCALE = 4000.0
DESIRED_CONTENT_BOOST = 0.10
AVOID_CONTENT_PENALTY = 0.20
MAX_EMBEDDING_DIMENSIONS = 4096


@dataclass(frozen=True)
class VisualEmbedding:
    model: str
    dimensions: int
    vector: tuple[float, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not self.model
            or len(self.model) > 200
            or type(self.dimensions) is not int
            or not 1 <= self.dimensions <= MAX_EMBEDDING_DIMENSIONS
            or len(self.vector) != self.dimensions
        ):
            raise PhotoError("embedding_invalid")
        try:
            values = tuple(float(x) for x in self.vector)
            if not all(math.isfinite(x) for x in values):
                raise ValueError
            norm = math.sqrt(sum(x * x for x in values))
            if not math.isfinite(norm) or norm <= 0:
                raise ValueError
            packed = struct.pack(f"<{self.dimensions}f", *(x / norm for x in values))
            object.__setattr__(self, "vector", struct.unpack(f"<{self.dimensions}f", packed))
        except (TypeError, ValueError, OverflowError, struct.error):
            raise PhotoError("embedding_invalid") from None


def serialize_embedding(value: VisualEmbedding) -> bytes:
    return struct.pack(f"<{value.dimensions}f", *value.vector)


def deserialize_embedding(data: bytes, model: str, dimensions: int) -> VisualEmbedding:
    if (
        type(dimensions) is not int
        or not 1 <= dimensions <= MAX_EMBEDDING_DIMENSIONS
        or len(data) != dimensions * 4
    ):
        raise PhotoError("embedding_invalid")
    values = struct.unpack(f"<{dimensions}f", data)
    if not all(math.isfinite(v) for v in values) or abs(sum(v * v for v in values) - 1) > 1e-4:
        raise PhotoError("embedding_invalid")
    return VisualEmbedding(model, dimensions, values)


def cosine_similarity(left: VisualEmbedding, right: VisualEmbedding) -> float:
    if left.model != right.model or left.dimensions != right.dimensions:
        raise PhotoError("embedding_incompatible")
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left.vector, right.vector, strict=True))))


class VisualEmbedder(Protocol):
    model: str
    dimensions: int

    async def embed_image(self, image_bytes: bytes) -> VisualEmbedding: ...


class FakeVisualEmbedder:
    model = "fake-visual-v1"
    dimensions = 3

    def __init__(self) -> None:
        self.calls = 0

    @timed("candidate_embedding")
    async def embed_image(self, image_bytes: bytes) -> VisualEmbedding:
        self.calls += 1
        values = hashlib.sha256(image_bytes).digest()[:3]
        return VisualEmbedding(self.model, self.dimensions, tuple(float(v) + 1 for v in values))


class OnnxCLIPEmbedder:
    """CPU-only optional ONNX adapter. No image/token leaves this process."""

    model = MODEL
    dimensions = 512

    def __init__(self, model_path: Path) -> None:
        self.path = model_path
        self.session: Any = None
        self.lock = asyncio.Lock()

    def _embed(self, data: bytes) -> VisualEmbedding:
        try:
            np = importlib.import_module("numpy")
            ort = importlib.import_module("onnxruntime")
            if self.session is None:
                if (
                    not self.path.is_file()
                    or hashlib.sha256(self.path.read_bytes()).hexdigest() != MODEL_SHA256
                ):
                    raise PhotoError("visual_model_unavailable")
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                self.session = ort.InferenceSession(
                    str(self.path), sess_options=options, providers=["CPUExecutionProvider"]
                )
            with Image.open(io.BytesIO(data)) as image:
                rgb = image.convert("RGB")
                scale = 224 / min(rgb.size)
                rgb = rgb.resize(
                    (int(rgb.width * scale), int(rgb.height * scale)), Image.Resampling.BICUBIC
                )
                x, y = (rgb.width - 224) // 2, (rgb.height - 224) // 2
                rgb = rgb.crop((x, y, x + 224, y + 224))
                pixels = np.asarray(rgb, dtype=np.float32) / 255.0
                pixels = (
                    pixels - np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
                ) / np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
                pixels = np.transpose(pixels, (2, 0, 1))[None, :, :, :]
            output = self.session.run(["image_embeds"], {"pixel_values": pixels})[0][0]
            return VisualEmbedding(self.model, self.dimensions, tuple(float(v) for v in output))
        except PhotoError:
            raise
        except Exception:
            raise PhotoError("visual_embedding_unavailable") from None

    @timed("candidate_embedding")
    async def embed_image(self, image_bytes: bytes) -> VisualEmbedding:
        async with self.lock:
            return await asyncio.to_thread(self._embed, image_bytes)


@dataclass(frozen=True)
class VisualScore:
    best_similarity: float | None
    top_k_mean: float | None
    reference_count: int


class CommunityVisualRanker:
    def score(self, candidate: VisualEmbedding, references: list[VisualEmbedding]) -> VisualScore:
        matches = sorted(
            (
                cosine_similarity(candidate, r)
                for r in references
                if r.model == candidate.model and r.dimensions == candidate.dimensions
            ),
            reverse=True,
        )
        if not matches:
            return VisualScore(None, None, 0)
        top = matches[:TOP_K]
        return VisualScore(matches[0], sum(top) / len(top), len(matches))


@dataclass(frozen=True)
class RankedPhoto:
    base_score: float
    visual_score: float | None
    final_score: float
    best_similarity: float | None
    reference_count: int
    metadata_score: float = 0
    quality_score: float = 0
    normalized_visual_score: float | None = None


def rank_photo(
    candidate: PhotoCandidate,
    search: PhotoSearch | PhotoQueryPlan,
    visual: VisualScore,
    desired: str | None = None,
    avoid: str | None = None,
    usage: int = 0,
) -> RankedPhoto:
    variants = search.ranking_variants if isinstance(search, PhotoQueryPlan) else (search,)
    base = max(PhotoRanker().score(candidate, query, usage=usage) for query in variants)
    if visual.top_k_mean is None:
        quality = min(min(candidate.width, candidate.height) / QUALITY_SHORT_SIDE_SCALE, 1.0) / (
            1 + usage
        )
        return RankedPhoto(
            base, None, base, None, 0, max(0.0, min(1.0, base / BASE_SCORE_SCALE)), quality
        )
    # Old metadata score's useful range is roughly 0..15; penalties remain explicit.
    metadata = max(0.0, min(1.0, base / BASE_SCORE_SCALE))
    tags = set(normalize_category(" ".join(candidate.tags)).split())
    wanted = set(normalize_category(desired).split())
    unwanted = set(normalize_category(avoid).split())
    desired_signal = len(wanted & tags) / max(1, len(wanted))
    avoid_signal = len(unwanted & tags) / max(1, len(unwanted))
    metadata = max(
        0.0,
        min(
            1.0,
            metadata
            + DESIRED_CONTENT_BOOST * desired_signal
            - AVOID_CONTENT_PENALTY * avoid_signal,
        ),
    )
    quality = min(min(candidate.width, candidate.height) / QUALITY_SHORT_SIDE_SCALE, 1.0) / (
        1 + usage
    )
    normalized_visual = (visual.top_k_mean + 1) / 2
    final = (
        VISUAL_WEIGHT * normalized_visual + METADATA_WEIGHT * metadata + QUALITY_WEIGHT * quality
    )
    return RankedPhoto(
        base,
        visual.top_k_mean,
        final,
        visual.best_similarity,
        visual.reference_count,
        metadata,
        quality,
        normalized_visual,
    )
