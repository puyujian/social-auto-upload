import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
from PIL import Image

from uploader.xiaohongshu_uploader.protocol import MediaInfo, XhsCreatorProtocol


class VideoLiveInputTests(unittest.TestCase):
    def test_mp4_is_uploaded_as_live_photo_without_animation_conversion(self):
        client = XhsCreatorProtocol.__new__(XhsCreatorProtocol)
        client.receipt = None
        client.wait_transcode = Mock()
        client._post_note = Mock(return_value={'success': True})
        uploads = []

        def upload(content, kind):
            uploads.append((kind, content))
            return MediaInfo('video' if kind == 'video' else f'image-{len(uploads)}',
                len(content), width=64, height=96, video_id='vid' if kind == 'video' else None)

        client.upload_media = upload
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory)/'live.mp4'
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 24, (64,96))
            self.assertTrue(writer.isOpened())
            for i in range(48):
                writer.write(np.full((96,64,3), i*4, dtype=np.uint8))
            writer.release()
            original = video.read_bytes()
            still = Path(directory)/'still.jpg'
            Image.new('RGB',(64,96),'blue').save(still)
            client.publish_note(image_files=[video,still],title='实况',note='正文',tags=[],confirm_publish=True)
        self.assertEqual([kind for kind,_ in uploads],['image','video','image'])
        self.assertEqual(uploads[1][1],original)
        with Image.open(io.BytesIO(uploads[0][1])) as cover:
            self.assertEqual(cover.size,(64,96))
        payload=client._post_note.call_args.args[0]
        images=payload['image_info']['images']
        self.assertEqual(images[0]['video_file_id'],'spectrum/video')
        self.assertNotIn('video_file_id',images[1])
        client.wait_transcode.assert_called_once_with('vid')


if __name__ == '__main__':
    unittest.main()
