import json
import unittest
from unittest.mock import Mock, patch

from uploader.xiaohongshu_uploader.protocol import (
    XhsCreatorProtocol, XiaohongshuTopicNotFoundError, split_trailing_topic_tags,
    build_topic_update_payload, common_payload,
)


class TopicFooterTests(unittest.TestCase):
    def test_footer_becomes_bound_topics_and_unsupported_tag_is_not_plain_text(self):
        client = XhsCreatorProtocol.__new__(XhsCreatorProtocol)
        def search(name):
            if name == "不存在的话题":
                raise XiaohongshuTopicNotFoundError(name)
            return {"id": "topic-" + name, "name": name, "link": "https://example/topic", "type": "topic"}
        client.search_topic = search
        payload = {"common": common_payload("normal", "标题", "正文原文。\n\n#麦芽糖 #不存在的话题 #叮叮糖")}
        binding = client._bind_topics(payload, ["麦芽糖"])
        self.assertEqual([t["name"] for t in payload["common"]["hash_tag"]], ["麦芽糖", "叮叮糖"])
        self.assertEqual(payload["common"]["desc"], "正文原文。\n\n#麦芽糖[话题]# #叮叮糖[话题]#")
        self.assertEqual(binding["skipped"], ["不存在的话题"])
        self.assertNotIn("不存在的话题", payload["common"]["desc"])

    def test_does_not_remove_inline_prose_hashtags(self):
        text = "喜欢#童年零食这个话题，也要看配料。"
        self.assertEqual(split_trailing_topic_tags(text), (text, []))

    def test_existing_rich_tags_are_not_duplicated(self):
        client = XhsCreatorProtocol.__new__(XhsCreatorProtocol)
        client.search_topic = lambda name: {"id": "id", "name": name, "link": "link", "type": "topic"}
        payload = {"common": common_payload("normal", "标题", "正文\n\n#麦芽糖[话题]#")}
        client._bind_topics(payload, ["麦芽糖", "#麦芽糖#"])
        self.assertEqual(payload["common"]["desc"].count("#麦芽糖[话题]#"), 1)

    def test_topic_update_preserves_note_identity_images_goods_and_prose(self):
        note = {"id": "note1", "type": "normal", "title": "原始标题", "desc": "原始正文。\n\n#麦芽糖",
            "ats": [], "privacy": {"type": 0}, "metadata_str": '{"goods":{}}',
            "images_list": [{"fileid": "spectrum/original", "width": 1000, "height": 1300}],
            "biz_relations": [{"biz_type": "GOODS_SELLER_V2", "biz_id": "sku1"}]}
        payload = build_topic_update_payload(note, {"noteCopyBind": {"copyable": False}})
        self.assertEqual(payload["common"]["note_id"], note["id"])
        self.assertEqual(payload["common"]["title"], note["title"])
        self.assertEqual(payload["common"]["biz_relations"], note["biz_relations"])
        self.assertEqual(payload["image_info"]["images"][0]["file_id"], "spectrum/original")
        self.assertFalse(json.loads(payload["common"]["business_binds"])["noteCopyBind"]["copyable"])

    def test_update_uses_existing_note_put_without_creation_only_rap_signature(self):
        from uploader.xiaohongshu_uploader import protocol
        client = XhsCreatorProtocol.__new__(XhsCreatorProtocol)
        client.cookies = {}
        client.query_note_detail = Mock(return_value={"id": "note1", "type": "normal", "title": "原题", "desc": "正文\n\n#麦芽糖",
            "images_list": [{"fileid": "original", "width": 100, "height": 130}]})
        client._get_note_api = Mock(return_value={})
        client.search_topic = lambda name: {"id": "topic1", "name": name, "link": "topic-link", "type": "topic"}
        client._signed_post_headers = lambda api, payload: ({"x-s": "signature"}, json.dumps(payload))
        client.session = Mock()
        client.session.put.return_value.status_code = 200
        client.session.put.return_value.json.return_value = {"success": True, "data": {"id": "note1"}}
        with patch.object(protocol, "generate_x_rap_param", side_effect=AssertionError("creation signature must not run")):
            result = client.update_note_topics("note1", confirm_update=True)
        self.assertEqual(result["note_id"], "note1")
        self.assertTrue(client.session.put.call_args.args[0].endswith("/note/update"))
        self.assertEqual(json.loads(client.session.put.call_args.kwargs["data"])["common"]["note_id"], "note1")


if __name__ == "__main__":
    unittest.main()
