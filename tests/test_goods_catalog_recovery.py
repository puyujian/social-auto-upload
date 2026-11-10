import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

from uploader.xiaohongshu_uploader import protocol


def response(data):
    r = requests.Response()
    r.status_code = 200
    r._content = json.dumps({"success": True, "data": data}).encode()
    return r


class GoodsCatalogRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cookie = Path(directory.name) / "account.json"
        self.cookie.write_text(json.dumps({"a1": "1" * 52, "web_session": "private"}))
        self.session = Mock(spec=requests.Session)
        self.session.cookies = requests.cookies.RequestsCookieJar()
        self.client = protocol.XhsCreatorProtocol(self.cookie, session=self.session)
        self.client.cookies["x-user-id-creator.xiaohongshu.com"] = "owner-1"
        self.goods = {"goods_info": {
            "goods_id": "sku-1", "item_id": "item-1", "name": "candy",
            "goods_seller_type": "GOODS_SELLER", "token": "must-not-persist",
        }}
        protocol.remember_goods(self.client, [self.goods])

    def permission(self, allowed=True):
        return response({"publish_permissions": [{"type": 1, "permission": allowed}]})

    def sku(self, **overrides):
        info = {
            "sku_id": "sku-1", "item_id": "item-1", "sku_name": "candy 250g",
            "sku_status": 1, "goods_seller_type": "GOODS_SELLER",
        }
        info.update(overrides)
        return response({"sku_info_map": {"sku-1": info}})

    def test_null_catalog_recovers_only_freshly_validated_original_sku(self):
        self.session.post.side_effect = [
            response(None), response({"type": "at"}), response(None),
            self.permission(), self.sku(),
        ]
        goods = self.client.query_goods_list("candy")
        self.assertEqual(goods[0]["goods_info"]["goods_id"], "sku-1")
        calls = self.session.post.call_args_list
        self.assertEqual(len(calls), 5)
        self.assertTrue(calls[-2].args[0].endswith("/permission"))
        self.assertTrue(calls[-1].args[0].endswith("/sku_infos"))
        self.assertEqual(json.loads(calls[-1].kwargs["data"]), {"sku_ids": ["sku-1"]})
        self.assertFalse(any(c.args[0].endswith("/note") for c in calls))

    def test_identifiers_are_not_a_permission_bypass(self):
        self.session.post.return_value = self.permission(False)
        with self.assertRaises(protocol.XiaohongshuGoodsUnavailableError):
            protocol.recover_cached_goods(self.client, "candy")
        self.assertEqual(self.session.post.call_count, 1)

    def test_inactive_renamed_wrong_item_or_wrong_sku_cannot_recover(self):
        for invalid in [
            {"sku_status": 0}, {"sku_id": "other-sku"}, {"item_id": "other-item"},
            {"sku_name": "unrelated product"}, {"goods_seller_type": "OTHER"},
        ]:
            with self.subTest(invalid=invalid):
                self.session.post.side_effect = [self.permission(), self.sku(**invalid)]
                with self.assertRaises(protocol.XiaohongshuGoodsUnavailableError):
                    protocol.recover_cached_goods(self.client, "candy")

    def test_cache_cannot_cross_account_or_match_unrelated_keywords(self):
        with self.assertRaises(protocol.XiaohongshuGoodsUnavailableError):
            protocol.recover_cached_goods(self.client, "tea")
        self.client.cookies["x-user-id-creator.xiaohongshu.com"] = "other-owner"
        with self.assertRaises(protocol.XiaohongshuGoodsUnavailableError):
            protocol.recover_cached_goods(self.client, "candy")
        self.session.post.assert_not_called()

    def test_missing_or_malformed_fresh_sku_map_keeps_hold(self):
        for data in [{}, {"sku_info_map": []}, {"sku_info_map": {}}]:
            with self.subTest(data=data):
                self.session.post.side_effect = [self.permission(), response(data)]
                with self.assertRaises(protocol.XiaohongshuGoodsUnavailableError):
                    protocol.recover_cached_goods(self.client, "candy")

    def test_cache_merges_goods_and_does_not_store_credentials(self):
        protocol.remember_goods(self.client, [{"goods_info": {
            "goods_id": "sku-2", "item_id": "item-2", "name": "tea",
        }}])
        data = self.cookie.with_suffix(".goods.json").read_text()
        self.assertNotIn("owner-1", data)
        self.assertNotIn("must-not-persist", data)
        self.assertNotIn("private", data)
        self.assertEqual(len(protocol._read_goods_cache(self.client)), 2)

    def test_first_login_reference_is_not_usable_without_fresh_permission(self):
        self.cookie.with_suffix(".goods.json").unlink()
        self.cookie.with_suffix(".goods.reference.json").write_text(json.dumps({"goods": [self.goods]}))
        self.session.post.return_value = self.permission(False)
        with self.assertRaises(protocol.XiaohongshuGoodsUnavailableError):
            protocol.recover_cached_goods(self.client, "candy")
        self.assertFalse(self.cookie.with_suffix(".goods.json").exists())
        self.session.post.side_effect = [self.permission(), self.sku()]
        protocol.recover_cached_goods(self.client, "candy")
        self.assertTrue(self.cookie.with_suffix(".goods.json").exists())
        self.client.cookies["x-user-id-creator.xiaohongshu.com"] = "different-owner"
        self.assertEqual(protocol._read_goods_cache(self.client), [])

    def test_reference_cannot_be_used_without_authenticated_creator(self):
        self.cookie.with_suffix(".goods.json").unlink()
        self.cookie.with_suffix(".goods.reference.json").write_text(json.dumps({"goods": [self.goods]}))
        self.client.cookies.pop("x-user-id-creator.xiaohongshu.com")
        with self.assertRaises(protocol.XiaohongshuGoodsUnavailableError):
            protocol.recover_cached_goods(self.client, "candy")
        self.session.post.assert_not_called()

