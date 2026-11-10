import time
import unittest
from unittest.mock import Mock

from uploader.xiaohongshu_uploader.protocol import XhsCreatorProtocol, XiaohongshuProtocolError


class UploadPermitTests(unittest.TestCase):
    def test_check_requires_usable_unexpired_permit(self):
        client = XhsCreatorProtocol.__new__(XhsCreatorProtocol)
        payload = {"success": True, "data": {"uploadTempPermits": [{
            "token": "test-token", "fileIds": ["test-file"], "expireTime": int(time.time()) + 3600,
        }]}}
        client.request_upload_permit = Mock(return_value=payload)
        self.assertEqual(client.validate_login_state(), payload)
        for invalid in [
            {"success": True}, {"data": {"uploadTempPermits": []}},
            {"data": {"uploadTempPermits": [{"token": "", "fileIds": ["a"], "expireTime": "9999999999"}]}},
            {"data": {"uploadTempPermits": [{"token": "a", "fileIds": [], "expireTime": "9999999999"}]}},
            {"data": {"uploadTempPermits": [{"token": "a", "fileIds": ["b"], "expireTime": "1"}]}},
        ]:
            client.request_upload_permit.return_value = invalid
            with self.assertRaises(XiaohongshuProtocolError):
                client.validate_login_state()
