import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from uploader.xiaohongshu_uploader.protocol import XhsCreatorProtocol


class PublishReceiptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cookie = Path(self.directory.name) / 'account.json'
        self.cookie.write_text(json.dumps({'a1': '1' * 52}))
        self.session = Mock()
        self.client = XhsCreatorProtocol(self.cookie, session=self.session, idempotency_key='same-note')
        self.client._signed_post_headers = Mock(return_value=({}, '{}'))
        self.signature = patch('uploader.xiaohongshu_uploader.protocol.generate_x_rap_param', return_value='signed')
        self.signature.start()
        self.addCleanup(self.signature.stop)

    def response(self, success=True):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'success': success, 'data': {'id': 'note-1'}}).encode()
        return response

    def post(self):
        return self.client._post_note({'common': {'hash_tag': [], 'desc': ''}}, tags=[], confirm_publish=True)

    def test_success_receipt_survives_process_restart_and_prevents_another_post(self):
        self.session.post.return_value = self.response()
        result = self.post()
        restarted = XhsCreatorProtocol(self.cookie, session=self.session, idempotency_key='same-note')
        assert restarted.publish_note(image_files=[], title='same', note='', tags=[]) == result
        assert self.session.post.call_count == 1

    def test_explicit_rejection_and_connect_timeout_allow_retry(self):
        self.session.post.side_effect = [self.response(False), requests.ConnectTimeout('not connected'), self.response()]
        with self.assertRaises(RuntimeError): self.post()
        with self.assertRaises(requests.ConnectTimeout): self.post()
        assert self.post()['success']
        assert self.session.post.call_count == 3

    def test_ambiguous_read_timeout_does_not_repeat_the_public_post(self):
        self.session.post.side_effect = requests.ReadTimeout('response lost')
        with self.assertRaises(requests.ReadTimeout): self.post()
        with self.assertRaisesRegex(RuntimeError, '发布结果待确认'): self.post()
        assert self.session.post.call_count == 1

    def test_explicit_http_rejection_can_be_retried(self):
        rejected = self.response(False)
        rejected.status_code = 429
        self.session.post.side_effect = [rejected, self.response()]
        with self.assertRaises(RuntimeError): self.post()
        assert self.post()['success']
        assert self.session.post.call_count == 2


if __name__ == '__main__':
    unittest.main()
