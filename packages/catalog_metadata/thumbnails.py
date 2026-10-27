"""Deterministic image derivatives, independent of sources and catalog consumers."""

import hashlib
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from .models import MetadataError

TRANSFORM = {"version": "lw.thumbnail/1", "format": "webp", "maxEdge": 384, "quality": 78, "method": 4}
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 512 * 1024


def create_thumbnail(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_INPUT_BYTES:
        raise MetadataError("invalid_preview", "Preview input must contain 1 byte through 2 MiB")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in ("PNG", "JPEG", "WEBP"):
                raise MetadataError("invalid_preview", "Preview must be PNG, JPEG or WebP")
            width, height = source.size
            if width * height > MAX_PIXELS:
                raise MetadataError("preview_pixel_limit", "Preview exceeds 16 megapixels")
            source.verify()
        with Image.open(BytesIO(data)) as source:
            source.seek(0)
            source.load()
            original = {"format": source.format, "width": width, "height": height, "bytes": len(data)}
            alpha = "A" in source.getbands() or "transparency" in source.info
            image = ImageOps.exif_transpose(source).convert("RGBA" if alpha else "RGB")
            image.thumbnail((TRANSFORM["maxEdge"], TRANSFORM["maxEdge"]), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="WEBP", quality=TRANSFORM["quality"], method=TRANSFORM["method"])
            encoded = output.getvalue()
            if len(encoded) > MAX_OUTPUT_BYTES:
                raise MetadataError("thumbnail_byte_limit", "Thumbnail exceeds 512 KiB")
            return encoded, {
                "state": "available",
                "sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
                "width": image.width,
                "height": image.height,
                "sourceSha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "sourceImage": original,
                "transform": dict(TRANSFORM),
            }
    except MetadataError:
        raise
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise MetadataError("invalid_preview", "Preview image cannot be decoded safely") from exc
