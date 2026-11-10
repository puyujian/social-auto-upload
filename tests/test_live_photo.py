import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import cv2
from PIL import Image, ImageDraw

from uploader.xiaohongshu_uploader.live_photo import gif_to_live_photo, image_to_live_photo
from uploader.xiaohongshu_uploader.protocol import MediaInfo, XhsCreatorProtocol


def animation(size=(32, 48), durations=(100, 400, 200)):
    frames = [Image.new("RGBA", size, color) for color in ("red", "blue", "green")]
    output = io.BytesIO()
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=list(durations), loop=0,
    )
    return output.getvalue()


class LivePhotoTests(unittest.TestCase):
    def test_workflow_live_photo_preserves_a_single_source_playback(self):
        client = XhsCreatorProtocol.__new__(XhsCreatorProtocol)
        client.receipt = None
        client.wait_transcode = Mock()
        client._post_note = Mock(return_value={"success": True})
        videos = []

        def upload(content, media_type):
            if media_type == "video":
                videos.append(content)
                return MediaInfo("motion", len(content), video_id="motion")
            return MediaInfo("cover", len(content), width=32, height=48)

        client.upload_media = upload
        with tempfile.TemporaryDirectory() as directory:
            gif = Path(directory) / "source.gif"
            gif.write_bytes(animation())
            client.publish_note(
                image_files=[gif], title="实况", note="正文", tags=[],
                confirm_publish=True, live_photo=True,
            )
            self.assertEqual(len(videos), 1)
            video = Path(directory) / "uploaded.mp4"
            video.write_bytes(videos[0])
            capture = cv2.VideoCapture(str(video))
            try:
                self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_COUNT), 21)
                self.assertEqual(capture.get(cv2.CAP_PROP_FPS), 30)
            finally:
                capture.release()

    def test_short_native_animation_plays_once_at_original_speed(self):
        _, video = gif_to_live_photo(animation(durations=(30, 30, 30)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "loop.mp4"
            path.write_bytes(video)
            capture = cv2.VideoCapture(str(path))
            try:
                self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_COUNT), 3)
                self.assertEqual(capture.get(cv2.CAP_PROP_FPS), 30)
            finally:
                capture.release()

    def test_static_image_creates_sixty_motion_frames(self):
        import math
        image = Image.new("RGB", (320, 480), "white")
        ImageDraw.Draw(image).ellipse((145, 225, 175, 255), fill="blue")
        source = io.BytesIO()
        image.save(source, format="PNG")
        cover, video = image_to_live_photo(source.getvalue())
        with Image.open(io.BytesIO(cover)) as still:
            self.assertEqual(still.size, (320, 480))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.mp4"
            path.write_bytes(video)
            capture = cv2.VideoCapture(str(path))
            try:
                self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_COUNT), 60)
                self.assertEqual(capture.get(cv2.CAP_PROP_FPS), 30)
                for index in range(60):
                    ok, pixels = capture.read()
                    self.assertTrue(ok)
                    mask = (pixels[:, :, 0] > 160) & (pixels[:, :, 1] < 120) & (pixels[:, :, 2] < 120)
                    yy, xx = mask.nonzero()
                    angle = 2 * math.pi * index / 60
                    self.assertAlmostEqual(xx.mean(), 160 + 3.2 * math.cos(angle), delta=0.8)
                    self.assertAlmostEqual(yy.mean(), 240 + 4.8 * math.sin(angle), delta=0.8)
            finally:
                capture.release()

    def test_conversion_keeps_each_motion_frame_of_30_fps_gif(self):
        frames = []
        for index in range(60):
            frame = Image.new("RGB", (160, 64), "white")
            ImageDraw.Draw(frame).rectangle(
                (5 + 2 * index, 24, 12 + 2 * index, 31), fill="blue",
            )
            frames.append(frame)
        source = io.BytesIO()
        frames[0].save(
            source, format="GIF", save_all=True, append_images=frames[1:],
            duration=[30, 30, 40] * 20, loop=0,
        )
        _, video = gif_to_live_photo(source.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "animation.mp4"
            path.write_bytes(video)
            capture = cv2.VideoCapture(str(path))
            positions = []
            try:
                self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_COUNT), 60)
                self.assertEqual(capture.get(cv2.CAP_PROP_FPS), 30)
                while True:
                    ok, pixels = capture.read()
                    if not ok:
                        break
                    blue = (
                        (pixels[:, :, 0] > 150)
                        & (pixels[:, :, 1] < 120)
                        & (pixels[:, :, 2] < 120)
                    )
                    positions.append(blue.nonzero()[1].mean())
            finally:
                capture.release()
        self.assertEqual(len(positions), 60)
        for index, position in enumerate(positions):
            self.assertAlmostEqual(position, 8.5 + 2 * index, delta=0.75)

    def test_conversion_preserves_variable_frame_timing(self):
        cover, video = gif_to_live_photo(animation())
        with Image.open(io.BytesIO(cover)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (32, 48))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "animation.mp4"
            path.write_bytes(video)
            capture = cv2.VideoCapture(str(path))
            try:
                self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_COUNT), 21)
                self.assertEqual(capture.get(cv2.CAP_PROP_FPS), 30)
                capture.set(cv2.CAP_PROP_POS_FRAMES, 4)
                ok, pixels = capture.read()
                self.assertTrue(ok)
                blue, green, red = pixels[24, 16]
                self.assertGreater(int(blue), int(red) + 150)
                self.assertGreater(int(blue), int(green) + 150)
            finally:
                capture.release()

    def test_cover_and_video_share_even_dimensions(self):
        cover, video = gif_to_live_photo(animation(size=(31, 47)))
        with Image.open(io.BytesIO(cover)) as image:
            self.assertEqual(image.size, (32, 48))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "animation.mp4"
            path.write_bytes(video)
            capture = cv2.VideoCapture(str(path))
            try:
                self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_WIDTH), 32)
                self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_HEIGHT), 48)
            finally:
                capture.release()

    def test_publish_converts_gif_and_keeps_mixed_image_order(self):
        client = XhsCreatorProtocol.__new__(XhsCreatorProtocol)
        client.receipt = None
        client.wait_transcode = Mock()
        client._post_note = Mock(return_value={"success": True})
        uploaded = []

        def upload(content, media_type):
            uploaded.append(media_type)
            file_id = f"file-{len(uploaded)}"
            if media_type == "video":
                return MediaInfo(file_id, len(content), video_id="video-2")
            with Image.open(io.BytesIO(content)) as image:
                return MediaInfo(
                    file_id, len(content), width=image.width, height=image.height,
                    mime_type=Image.MIME[image.format],
                )

        client.upload_media = upload
        with tempfile.TemporaryDirectory() as directory:
            gif = Path(directory) / "animated.gif"
            gif.write_bytes(animation())
            still = Path(directory) / "still.png"
            Image.new("RGB", (32, 48), "white").save(still)
            result = client.publish_note(
                image_files=[gif, still], title="实况", note="正文",
                tags=["测试"], confirm_publish=True,
            )
        self.assertTrue(result["success"])
        self.assertEqual(uploaded, ["image", "video", "image"])
        client.wait_transcode.assert_called_once_with("video-2")
        payload = client._post_note.call_args.args[0]
        images = payload["image_info"]["images"]
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0]["video_file_id"], "spectrum/file-2")
        self.assertEqual(json.loads(images[0]["extra_info_json"])["mimeType"], "image/jpeg")
        self.assertNotIn("video_file_id", images[1])
        self.assertEqual(payload["common"]["type"], "normal")
