from pathlib import Path
import tempfile
import unittest

from uploader.base_video import BaseVideoUploader


class BaseVideoUploaderTests(unittest.TestCase):
    def test_validate_video_file_rejects_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "empty.mp4"
            video_path.write_bytes(b"")

            with self.assertRaisesRegex(ValueError, "视频文件为空"):
                BaseVideoUploader.validate_video_file(video_path)

    def test_validate_video_file_accepts_non_empty_video(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            video_path.write_bytes(b"video")

            result = BaseVideoUploader.validate_video_file(video_path)

        self.assertEqual(result.name, "demo.mp4")


if __name__ == "__main__":
    unittest.main()
