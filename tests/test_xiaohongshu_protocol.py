import json
import tempfile
import unittest
from pathlib import Path

from uploader.xiaohongshu_uploader import protocol


class XiaohongshuProtocolTests(unittest.TestCase):
    def test_load_cookie_state_reads_storage_state_without_exposing_values(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cookie_path = Path(tmp_dir) / "xiaohongshu_creator.json"
            cookie_path.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "a1",
                                "value": "secret-a1",
                                "domain": ".xiaohongshu.com",
                                "path": "/",
                            },
                            {
                                "name": "web_session",
                                "value": "secret-session",
                                "domain": ".xiaohongshu.com",
                                "path": "/",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            cookies = protocol.load_cookie_state(cookie_path)

        self.assertEqual(cookies["a1"], "secret-a1")
        self.assertEqual(cookies["web_session"], "secret-session")

    def test_load_cookie_state_requires_a1(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cookie_path = Path(tmp_dir) / "xiaohongshu_creator.json"
            cookie_path.write_text(json.dumps({"cookies": [{"name": "web_session", "value": "secret"}]}))

            with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "a1"):
                protocol.load_cookie_state(cookie_path)

    def test_build_ros_signature_matches_reference_formula(self):
        message = "1710000000;1720000000"
        file_id = "test-file-id"
        content_length = 12345
        host = "ros-upload.xiaohongshu.com"

        key = protocol.hmac.new(b"null", message.encode("utf-8"), protocol.hashlib.sha1).hexdigest()
        params_hash = protocol.hashlib.sha1(
            f"put\n/spectrum/{file_id}\n\ncontent-length={content_length}&host={host}\n".encode("utf-8")
        ).hexdigest()
        expected = protocol.hmac.new(
            key.encode("utf-8"),
            f"sha1\n{message}\n{params_hash}\n".encode("utf-8"),
            protocol.hashlib.sha1,
        ).hexdigest()

        self.assertEqual(protocol.build_ros_signature(message, file_id, content_length, host), expected)

    def test_creator_signer_uses_creator_appid(self):
        signer = protocol.CreatorSigner({"a1": "1" * 52})
        signed = signer.sign_get(
            protocol.splice_api(
                "/api/media/v1/upload/creator/permit",
                {
                    "biz_name": "spectrum",
                    "scene": "video",
                    "file_count": "1",
                    "version": "1",
                    "source": "web",
                },
            )
        )

        self.assertTrue(signed["x-s"].startswith("XYS_"))
        self.assertIn("x-s-common", signed)
        self.assertEqual(len(signed["x-b3-traceid"]), 16)
        self.assertEqual(len(signed["x-xray-traceid"]), 32)

    def test_post_note_requires_explicit_confirmation(self):
        class FakeProtocol(protocol.XhsCreatorProtocol):
            def __init__(self):
                self.cookies = {"a1": "1" * 52}
                self.signer = protocol.CreatorSigner(self.cookies)

            def search_topic(self, topic):
                raise AssertionError("no topics expected")

        payload = {
            "common": {
                "desc": "正文",
                "hash_tag": [],
            }
        }

        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "确认"):
            FakeProtocol()._post_note(payload, tags=[], group_chat="", confirm_publish=False)

    def test_common_payload_defaults_to_public_visibility(self):
        payload = protocol.common_payload("video", "标题", "正文")

        self.assertEqual(payload["privacy_info"]["type"], protocol.XHS_PRIVACY_PUBLIC)
        self.assertEqual(payload["privacy_info"]["op_type"], 1)

    def test_extract_group_list_accepts_live_snake_case_shape(self):
        payload = {
            "success": True,
            "data": {
                "group_list": [
                    {
                        "group_id": "gid-1",
                        "group_name": "手串定制",
                        "desc": "12人",
                        "avatar": "https://example/avatar.png",
                        "linkable": True,
                    }
                ]
            },
        }

        groups = protocol.extract_group_list(payload)
        selected = protocol.select_group_for_bind(groups, "手串")
        bind = protocol.build_group_bind(selected, "手串")

        self.assertEqual(bind["groupId"], "gid-1")
        self.assertEqual(bind["groupName"], "手串定制")
        self.assertEqual(bind["desc"], "12人")
        self.assertEqual(bind["avatar"], "https://example/avatar.png")

    def test_select_group_requires_unique_partial_match(self):
        groups = [
            {"group_id": "gid-1", "group_name": "手串定制", "linkable": True},
            {"group_id": "gid-2", "group_name": "手串交流", "linkable": True},
        ]

        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "不唯一"):
            protocol.select_group_for_bind(groups, "手串")

    def test_select_group_requires_unique_exact_match(self):
        groups = [
            {"group_id": "gid-1", "group_name": "手串", "linkable": True},
            {"group_id": "gid-2", "group_name": "手串", "linkable": True},
        ]

        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "不唯一"):
            protocol.select_group_for_bind(groups, "手串")

    def test_select_group_rejects_unlinkable_group(self):
        groups = [{"group_id": "gid-1", "group_name": "手串定制", "linkable": False}]

        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "不可关联"):
            protocol.select_group_for_bind(groups, "手串定制")

    def test_inject_group_bind_preserves_business_binds_fields(self):
        payload = {
            "common": {
                "business_binds": protocol._compact_json(
                    {
                        "version": 1,
                        "notePostTiming": {},
                        "noteCopyBind": {"copyable": True},
                        "optionRelationList": [],
                    }
                )
            }
        }
        group_bind = {
            "groupId": "gid-1",
            "groupName": "手串定制",
            "desc": "12人",
            "avatar": "https://example/avatar.png",
        }

        protocol.inject_group_bind(payload, group_bind)

        binds = json.loads(payload["common"]["business_binds"])
        self.assertEqual(binds["groupBind"], group_bind)
        self.assertEqual(binds["noteCopyBind"], {"copyable": True})
        self.assertEqual(binds["optionRelationList"], [])


if __name__ == "__main__":
    unittest.main()
