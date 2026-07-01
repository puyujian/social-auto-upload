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

    def test_creator_signer_reports_invalid_byte_constants_readably(self):
        class BrokenSigner(protocol.CreatorSigner):
            A3_PREFIX = {"bad": "shape"}

        signer = BrokenSigner({"a1": "1" * 52})

        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "A3_PREFIX"):
            signer.sign_get("/api/media/v1/upload/creator/permit")

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

    def test_post_note_keeps_topic_query_failures_fatal(self):
        class FakeProtocol(protocol.XhsCreatorProtocol):
            def __init__(self):
                self.cookies = {"a1": "1" * 52}
                self.signer = protocol.CreatorSigner(self.cookies)

            def search_topic(self, topic):
                raise protocol.XiaohongshuProtocolError(f"查询小红书话题失败: {topic}")

        payload = {
            "common": {
                "desc": "正文",
                "hash_tag": [],
            }
        }

        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "查询小红书话题失败"):
            FakeProtocol()._post_note(
                payload,
                tags=["回老家学艺"],
                group_chat="",
                product_keywords=[],
                confirm_publish=True,
            )

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

    def test_build_goods_relations_matches_captured_publish_payload(self):
        goods = [
            {
                "goods_info": {
                    "goods_id": "67c15dafd684d70015c8bee0",
                    "name": "儿时麦芽糖",
                    "goods_seller_type": "GOODS_SELLER",
                }
            },
            {
                "goods_info": {
                    "goods_id": "69a83eb0b8f98300010f3167",
                    "name": "非遗手工麦芽糖",
                    "goods_seller_type": "GOODS_SELLER",
                }
            },
        ]

        relations = protocol.build_goods_relations(goods)

        self.assertEqual(relations[0]["biz_type"], "GOODS_SELLER_V2")
        self.assertEqual(relations[0]["biz_id"], "67c15dafd684d70015c8bee0")
        first_extra = json.loads(relations[0]["extra_info"])
        second_extra = json.loads(relations[1]["extra_info"])
        self.assertEqual(first_extra["goods_type"], "goods_seller")
        self.assertEqual(first_extra["tab_id"], 1)
        self.assertEqual(first_extra["image_type"], "spec")
        self.assertEqual(first_extra["left_bottom_type"], "BUY_GOODS")
        self.assertEqual(first_extra["bind_order"], 0)
        self.assertEqual(second_extra["bind_order"], 1)

    def test_select_goods_uses_first_match_like_browser_picker(self):
        goods = [
            {"goods_info": {"goods_id": "goods-1", "name": "非遗麦芽糖"}},
            {"goods_info": {"goods_id": "goods-2", "name": "非遗梨膏糖"}},
        ]

        selected = protocol.select_goods_for_bind(goods, "非遗")

        self.assertEqual(selected["goods_info"]["goods_id"], "goods-1")

    def test_inject_goods_relations_sets_live_preheat_goods_info(self):
        payload = {"common": {"biz_relations": []}}
        relation = {
            "biz_type": "GOODS_SELLER_V2",
            "biz_id": "goods-1",
            "extra_info": "{}",
        }

        protocol.inject_goods_relations(payload, [relation])

        self.assertEqual(payload["common"]["goods_info"], {"extension": {"live_preheat": "0"}})
        self.assertEqual(payload["common"]["biz_relations"], [relation])

    def test_post_note_injects_goods_relations_before_confirmation_gate(self):
        class FakeProtocol(protocol.XhsCreatorProtocol):
            def __init__(self):
                self.cookies = {"a1": "1" * 52}
                self.signer = protocol.CreatorSigner(self.cookies)

            def search_topic(self, topic):
                raise AssertionError("no topics expected")

            def resolve_goods_bindings(self, product_keywords):
                self.seen_products = product_keywords
                return [
                    {
                        "biz_type": "GOODS_SELLER_V2",
                        "biz_id": "goods-1",
                        "extra_info": "{}",
                    }
                ]

        payload = {"common": {"desc": "正文", "hash_tag": [], "biz_relations": []}}
        fake = FakeProtocol()

        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "确认"):
            fake._post_note(
                payload,
                tags=[],
                group_chat="",
                product_keywords=["儿时"],
                confirm_publish=False,
            )

        self.assertEqual(fake.seen_products, ["儿时"])
        self.assertEqual(payload["common"]["goods_info"], {"extension": {"live_preheat": "0"}})
        self.assertEqual(payload["common"]["biz_relations"][0]["biz_id"], "goods-1")

    def test_inject_topic_trace_matches_browser_context_shape(self):
        payload = protocol.build_image_note_payload(
            title="标题",
            description="正文",
            file_infos=[{"fileIds": "file-1", "file_size": 100, "width": 1, "height": 1}],
        )
        topic = {"id": "topic-1", "name": "麦芽糖", "link": "", "type": "topic"}

        protocol.inject_topic_trace(payload, topic)

        context = json.loads(payload["common"]["capa_trace_info"]["contextJson"])
        self.assertEqual(
            context["recommend_topics"]["used"],
            [{"id": "topic-1", "name": "麦芽糖", "topic_source": "search_offical"}],
        )

    def test_post_note_limits_topics_to_ten(self):
        class FakeProtocol(protocol.XhsCreatorProtocol):
            def __init__(self):
                self.cookies = {"a1": "1" * 52}
                self.signer = protocol.CreatorSigner(self.cookies)

            def search_topic(self, topic):
                return {"id": topic, "name": topic, "link": "", "type": "topic"}

        payload = protocol.build_image_note_payload(
            title="标题",
            description="正文",
            file_infos=[{"fileIds": "file-1", "file_size": 100, "width": 1, "height": 1}],
        )
        tags = [f"tag-{index}" for index in range(12)]

        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "确认"):
            FakeProtocol()._post_note(
                payload,
                tags=tags,
                group_chat="",
                product_keywords=[],
                confirm_publish=False,
            )

        self.assertEqual(len(payload["common"]["hash_tag"]), 10)
        self.assertIn("#tag-9[话题]#", payload["common"]["desc"])
        self.assertNotIn("#tag-10[话题]#", payload["common"]["desc"])
        self.assertIn("\n\n#tag-0[话题]# #tag-1[话题]#", payload["common"]["desc"])
        self.assertNotIn("#tag-0[话题]#\n\n#tag-1[话题]#", payload["common"]["desc"])

    def test_post_note_keeps_text_topic_when_official_topic_not_found(self):
        class FakeProtocol(protocol.XhsCreatorProtocol):
            def __init__(self):
                self.cookies = {"a1": "1" * 52}
                self.signer = protocol.CreatorSigner(self.cookies)

            def search_topic(self, topic):
                if topic == "麦芽糖泡牛奶":
                    raise protocol.XiaohongshuTopicNotFoundError(f"未找到小红书话题: {topic}")
                return {"id": "topic-1", "name": topic, "link": "", "type": "topic"}

        payload = protocol.build_image_note_payload(
            title="标题",
            description="正文",
            file_infos=[{"fileIds": "file-1", "file_size": 100, "width": 1, "height": 1}],
        )

        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "确认"):
            FakeProtocol()._post_note(
                payload,
                tags=["#麦芽糖泡牛奶", "麦芽糖"],
                group_chat="",
                product_keywords=[],
                confirm_publish=False,
            )

        self.assertEqual(payload["common"]["hash_tag"], [{"id": "topic-1", "name": "麦芽糖", "link": "", "type": "topic"}])
        self.assertIn("\n\n#麦芽糖泡牛奶[话题]# #麦芽糖[话题]#", payload["common"]["desc"])
        context = json.loads(payload["common"]["capa_trace_info"]["contextJson"])
        self.assertEqual(
            context["recommend_topics"]["used"],
            [{"id": "topic-1", "name": "麦芽糖", "topic_source": "search_offical"}],
        )

    def test_append_topics_to_desc_separates_body_from_single_tag_line(self):
        payload = {"common": {"desc": "正文第一段\n正文第二段"}}

        protocol.append_topics_to_desc(
            payload,
            [
                {"id": "topic-1", "name": "麦芽糖", "link": "", "type": "topic"},
                {"id": "topic-2", "name": "手工糖", "link": "", "type": "topic"},
            ],
        )

        self.assertEqual(
            payload["common"]["desc"],
            "正文第一段\n正文第二段\n\n#麦芽糖[话题]# #手工糖[话题]#",
        )


if __name__ == "__main__":
    unittest.main()
