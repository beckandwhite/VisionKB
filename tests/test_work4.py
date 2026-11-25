"""Operational-failure tests for work4 thumbnail generation (issue #93).

The thumbnail producer used to swallow every decode/IO failure behind an
``except (OSError, ValueError): pass`` and return a bare ``False``, so the
*reason* for a persistent failure (a corrupt HEIC, a full disk, an unreadable
source folder) never reached the tracker -- backend.run_task could only record a
generic ``"thumbnail generation failed"``. These tests assert the reason now
survives: a corrupt/unreadable source makes ``run()`` *re-raise* with the
filename + cause (and clean up the partial ``.part`` file), and that
``backend.run_task`` records the real cause in the tracker's ``last_error``.

The suite is hermetic: it patches ``work4.Image`` and forces ``_PIL_AVAILABLE``
so it runs identically with or without Pillow installed (the CI ``lint`` job
does not install Pillow) and never touches a real thumbnails directory.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import work4  # noqa: E402


def _image_cm(save_side_effect):
    """A stand-in PIL Image context manager whose ``save()`` runs ``save_side_effect``."""
    obj = mock.MagicMock()
    obj.thumbnail.return_value = None
    obj.mode = "RGB"
    obj.save.side_effect = save_side_effect
    ctx = mock.MagicMock()
    ctx.__enter__.return_value = obj
    ctx.__exit__.return_value = False
    return ctx


class Work4TestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="work4-"))
        self.addCleanup(shutil.rmtree, str(self._tmp), ignore_errors=True)
        self.source = {"filename": "corrupt.jpg", "source_key": "/src/corrupt.jpg"}
        self.config = {"thumbnails_dir": self._tmp / "thumbs"}


class TestThumbnailFailureSurfacesCause(Work4TestCase):
    """A corrupt/unreadable source must re-raise with the cause, not return False."""

    def test_decode_failure_reraises_with_filename_and_cause(self):
        with (
            mock.patch.object(work4, "_PIL_AVAILABLE", True),
            mock.patch.object(work4, "Image", create=True) as fake_image,
        ):
            fake_image.open.side_effect = OSError("could not decode image (corrupt)")
            with self.assertRaises(RuntimeError) as ctx:
                work4.run(self.source, self.config)
        message = str(ctx.exception)
        # The reason (filename + exception type + message) is in the error text.
        self.assertIn("corrupt.jpg", message)
        self.assertIn("OSError", message)
        self.assertIn("could not decode image (corrupt)", message)
        # The original exception is preserved as the cause chain for tracebacks.
        self.assertIsInstance(ctx.exception.__cause__, OSError)

    def test_value_error_is_also_surfaced(self):
        with (
            mock.patch.object(work4, "_PIL_AVAILABLE", True),
            mock.patch.object(work4, "Image", create=True) as fake_image,
        ):
            fake_image.open.side_effect = ValueError("unsupported image format")
            with self.assertRaises(RuntimeError) as ctx:
                work4.run(self.source, self.config)
        message = str(ctx.exception)
        self.assertIn("corrupt.jpg", message)
        self.assertIn("unsupported image format", message)
        self.assertIsInstance(ctx.exception.__cause__, ValueError)

    def test_partial_part_is_cleaned_up_on_failure(self):
        part = self.config["thumbnails_dir"] / "corrupt.jpg.part"
        destination = self.config["thumbnails_dir"] / "corrupt.jpg"

        def save_then_fail(*_args, **_kwargs):
            # A partial write that fails mid-way (e.g. full disk): the .part
            # exists on disk and must be cleaned up, with no destination left.
            part.write_bytes(b"partial-thumbnail-bytes")
            raise OSError("No space left on device")

        with (
            mock.patch.object(work4, "_PIL_AVAILABLE", True),
            mock.patch.object(work4, "Image", create=True) as fake_image,
        ):
            fake_image.open.return_value = _image_cm(save_then_fail)
            with self.assertRaises(RuntimeError):
                work4.run(self.source, self.config)
        self.assertFalse(part.exists(), "partial .part temp file was not cleaned up")
        self.assertFalse(destination.exists(), "no thumbnail destination may remain")

    def test_success_still_returns_true_without_raising(self):
        """The happy path is unchanged: a valid image yields a thumbnail + True."""

        def save_ok(*_args, **_kwargs):
            # save() writes to the .part, which work4 then os.replace()s to dest.
            _kwargs_part = _args[0]
            Path(_kwargs_part).write_bytes(b"valid-thumbnail-bytes")

        with (
            mock.patch.object(work4, "_PIL_AVAILABLE", True),
            mock.patch.object(work4, "Image", create=True) as fake_image,
        ):
            fake_image.open.return_value = _image_cm(save_ok)
            self.assertTrue(work4.run(self.source, self.config))
        destination = self.config["thumbnails_dir"] / "corrupt.jpg"
        self.assertTrue(destination.is_file(), "the generated thumbnail is missing")
        self.assertFalse(
            (self.config["thumbnails_dir"] / "corrupt.jpg.part").exists(),
            "no .part temp file should remain after a successful write",
        )


class TestBackendRecordsRealCause(Work4TestCase):
    """backend.run_task must record the actual cause, not the generic string."""

    def test_last_error_names_the_cause_not_the_generic_string(self):
        import backend

        task = {"source_key": "/src/corrupt.jpg", "work_name": "work4"}
        work = {"name": "work4", "handler": "work4"}
        with (
            mock.patch.object(work4, "_PIL_AVAILABLE", True),
            mock.patch.object(work4, "Image", create=True) as fake_image,
        ):
            fake_image.open.side_effect = OSError("could not decode image (corrupt)")
            ok, _elapsed = backend.run_task(self.source, task, work, self.config, "worker@host")
        self.assertFalse(ok, "a failed thumbnail must report ok=False")
        self.assertIn("corrupt.jpg", task["last_error"])
        self.assertIn("could not decode image (corrupt)", task["last_error"])
        self.assertNotEqual(task["last_error"], "thumbnail generation failed")


if __name__ == "__main__":
    unittest.main()
