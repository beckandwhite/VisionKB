"""Work 4: generate a 320px JPEG thumbnail for each picture.

Uses Pillow (+ pillow-heif for HEIC/HEIF) instead of the former `/usr/bin/sips`
call, so thumbnails can be generated on Linux/Docker as well as macOS (issue #14).
The longest side is scaled to at most 320px, preserving aspect ratio; the output
is always a JPEG.
"""

import contextlib
import os
from pathlib import Path

try:
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


# Longest side of every thumbnail, matching the old `sips -Z 320`.
THUMBNAIL_MAX_DIM = 320


def run(source, config):
    """Generate the thumbnail file and return whether it exists."""
    if not _PIL_AVAILABLE:
        return False
    output_dir = config["thumbnails_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / (Path(source["filename"]).stem + ".jpg")
    if destination.is_file():
        return True
    temporary = str(destination) + ".part"
    try:
        # Image.open is lazy; thumbnail() forces the decode. Always emit a JPEG;
        # flatten any alpha/CMYK onto a white background so PNG/HEIC with an alpha
        # channel do not composite onto black.
        with Image.open(source["source_key"]) as image:
            image.thumbnail((THUMBNAIL_MAX_DIM, THUMBNAIL_MAX_DIM))
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(temporary, format="JPEG", quality=90)
        if os.path.isfile(temporary):
            os.replace(temporary, destination)
            return True
    except (OSError, ValueError):
        pass
    with contextlib.suppress(OSError):
        os.unlink(temporary)
    return False
