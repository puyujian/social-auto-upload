import io
import json
import unittest
from unittest.mock import Mock

from PIL import Image

from uploader.xiaohongshu_uploader import protocol


class GifUploadTests(unittest.TestCase):
    def test_upload_preserves_animation_and_payload_mime(self):
        output = io.BytesIO()
        first = Image.new("RGB", (32, 48), "red")
        first.save(
            output, format="GIF", save_all=True,
            append_images=[Image.new("RGB", first.size, "blue")],
            duration=200, loop=0,
        )
        content = output.getvalue()
        client = protocol.XhsCreatorProtocol.__new__(protocol.XhsCreatorProtocol)
        client.cookies = {}
        client.session = Mock()
        client.request_upload_permit = Mock(return_value={
            "data": {"uploadTempPermits": [{
                "fileIds": ["spectrum/test-gif"], "expireTime": 9999999999, "token": "test",
            }]},
        })

        media = client.upload_media(content, "image")

        self.assertEqual((media.width, media.height), (32, 48))
        self.assertEqual(media.mime_type, "image/gif")
        sent = client.session.put.call_args.kwargs["data"]
        self.assertEqual(sent, content)
        with Image.open(io.BytesIO(sent)) as image:
            self.assertEqual(image.n_frames, 2)
        payload = protocol.build_image_note_payload(
            title="GIF", description="Animation", file_infos=[media.as_reference_dict()],
        )
        metadata = json.loads(payload["image_info"]["images"][0]["extra_info_json"])
        self.assertEqual(metadata["mimeType"], "image/gif")

    def test_static_image_dimensions_remain_supported(self):
        for image_format in ("PNG", "JPEG", "WEBP"):
            with self.subTest(image_format=image_format):
                output = io.BytesIO()
                Image.new("RGB", (120, 40)).save(output, format=image_format)
                self.assertEqual(protocol.read_image_size(output.getvalue()), (120, 60))
