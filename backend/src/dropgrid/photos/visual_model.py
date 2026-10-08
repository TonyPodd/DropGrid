"""Explicit pinned public model download; no token discovery/authentication."""

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

from dropgrid.config import Settings
from dropgrid.photos.visual import MODEL, MODEL_REVISION, MODEL_SHA256


def main() -> None:
    target = Settings().visual_model_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == MODEL_SHA256:
        print("Visual model already cached:", MODEL)
        return
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".model-")
    try:
        url = f"https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/{MODEL_REVISION}/onnx/vision_model_quantized.onnx"
        with urllib.request.urlopen(url, timeout=60) as response, os.fdopen(fd, "wb") as output:
            digest = hashlib.sha256()
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise ValueError
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != MODEL_SHA256:
            raise ValueError
        os.replace(temporary, target)
        print("Visual model cached:", MODEL, "bytes:", total)
    except Exception:
        raise SystemExit("Visual model download failed safely") from None
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
