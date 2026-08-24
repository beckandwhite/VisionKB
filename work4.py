"""Work 4: generate a 320px JPEG thumbnail for each picture."""

import os
import subprocess
from pathlib import Path


def run(source, config):
    """Generate the thumbnail file and return whether it exists."""
    output_dir = config["thumbnails_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / (Path(source["filename"]).stem + ".jpg")
    if destination.is_file():
        return True
    temporary = str(destination) + ".part"
    command = ["/usr/bin/sips", "-Z", "320"]
    if Path(source["filename"]).suffix.lower() == ".heic":
        command += ["-s", "format", "jpeg"]
    command += [source["source_key"], "--out", temporary]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=30)
        if completed.returncode == 0 and os.path.isfile(temporary):
            os.replace(temporary, destination)
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        os.unlink(temporary)
    except OSError:
        pass
    return False
