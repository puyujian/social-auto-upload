import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from uploader.xiaohongshu_uploader import protocol


def response(status=200, payload=None, *, raw=None):
    result = requests.Response()
    result.status_code = status
    result._content = raw if raw is not None else json.dumps(payload).encode()
    return result


def goods():
    return response(payload={"success": True, "data": {"data": [
        {"goods_info": {"goods_id": "sku-1", "name": "candy"}}
    ]}})


class GoodsQueryRetryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        cookie = Path(directory.name) / "account.json"
        cookie.write_text(json.dumps({"a1": "1" * 52, "web_session": "private-session"}))
        self.session = Mock(spec=requests.Session)
        self.session.cookies = requests.cookies.RequestsCookieJar()
        self.client = protocol.XhsCreatorProtocol(cookie, session=self.session)
        sleep = patch.object(protocol.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def query(self):
        return self.client.query_goods_list("candy")

    def test_gateway_error_retries_only_identical_lookup(self):
        self.session.post.side_effect = [response(502, raw=b"<html>bad gateway</html>"), goods()]
        self.assertEqual(len(self.query()), 1)
        self.assertEqual(self.session.post.call_args_list[0], self.session.post.call_args_list[1])
        self.assertTrue(self.session.post.call_args.args[0].endswith(protocol.XHS_GOODS_LIST_API))
        self.sleep.assert_called_once_with(1)

    def test_network_failures_are_retried(self):
        for error in (requests.ReadTimeout, requests.ConnectTimeout, requests.ConnectionError):
            with self.subTest(error=error.__name__):
                self.session.reset_mock()
                self.session.post.side_effect = [error("temporary transport failure"), goods()]
                self.assertEqual(len(self.query()), 1)
                self.assertEqual(self.session.post.call_count, 2)

    def test_rate_limit_has_a_finite_retry_budget(self):
        self.session.post.return_value = response(
            429, {"success": False, "code": 300, "msg": "rate limited"}
        )
        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "HTTP 429; code=300; rate limited"):
            self.query()
        self.assertEqual(self.session.post.call_count, protocol.GOODS_QUERY_MAX_ATTEMPTS)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1, 2, 4])

    def test_transport_error_does_not_expose_exception_or_cookies(self):
        self.session.post.side_effect = requests.ReadTimeout("private-session and private URL")
        with self.assertRaises(protocol.XiaohongshuProtocolError) as caught:
            self.query()
        self.assertIn("ReadTimeout", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.session.post.call_count, protocol.GOODS_QUERY_MAX_ATTEMPTS)

    def test_invalid_json_and_non_object_json_are_retried(self):
        for raw in (b"<html>upstream failure</html>", b"[]", b"null"):
            with self.subTest(raw=raw):
                self.session.reset_mock()
                self.session.post.side_effect = [response(raw=raw), goods()]
                self.assertEqual(len(self.query()), 1)
                self.assertEqual(self.session.post.call_count, 2)

    def test_http_200_login_error_uses_official_exchange(self):
        unauthorized = response(payload={"success": False, "code": -100, "msg": "not logged in"})
        self.session.post.side_effect = [unauthorized, response(payload={"success": True}), goods()]
        self.assertEqual(len(self.query()), 1)
        self.assertTrue(self.session.post.call_args_list[1].args[0].endswith("/service-ticket"))
        self.sleep.assert_not_called()

    def test_sso_propagation_delay_retries_without_another_exchange(self):
        unauthorized = response(401, {"success": False, "code": -100})
        self.session.post.side_effect = [
            unauthorized, response(payload={"success": True}), unauthorized, goods(),
        ]
        self.assertEqual(len(self.query()), 1)
        self.assertEqual(self.session.post.call_count, 4)
        exchanges = [c for c in self.session.post.call_args_list if c.args[0].endswith("/service-ticket")]
        self.assertEqual(len(exchanges), 1)
        self.sleep.assert_called_once_with(2)

    def test_authorization_shares_the_total_lookup_budget(self):
        unauthorized = response(401, {"success": False, "code": -100})
        self.session.post.side_effect = [
            response(503, {}), unauthorized, response(payload={"success": True}),
            response(502, {}), goods(),
        ]
        self.assertEqual(len(self.query()), 1)
        lookups = [c for c in self.session.post.call_args_list
                   if c.args[0].endswith(protocol.XHS_GOODS_LIST_API)]
        self.assertEqual(len(lookups), protocol.GOODS_QUERY_MAX_ATTEMPTS)

    def test_no_exchange_is_started_after_the_final_lookup(self):
        self.session.post.side_effect = [
            response(503, {}), response(503, {}), response(503, {}),
            response(401, {"success": False, "code": -100}),
        ]
        with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "HTTP 401"):
            self.query()
        self.assertEqual(self.session.post.call_count, protocol.GOODS_QUERY_MAX_ATTEMPTS)

    def test_permission_and_business_errors_are_not_retried(self):
        for status in (403, 400, 200):
            with self.subTest(status=status):
                self.session.reset_mock()
                self.sleep.reset_mock()
                self.session.post.side_effect = None
                self.session.post.return_value = response(
                    status, {"success": False, "code": 7001, "msg": "permission denied"}
                )
                with self.assertRaisesRegex(protocol.XiaohongshuProtocolError, "code=7001; permission denied"):
                    self.query()
                self.assertEqual(self.session.post.call_count, 1)
                self.sleep.assert_not_called()

    def test_empty_success_is_not_retried_or_treated_as_bound_goods(self):
        self.session.post.return_value = response(payload={"success": True, "data": {"data": []}})
        self.assertEqual(self.query(), [])
        self.assertEqual(self.session.post.call_count, 1)
        with self.assertRaises(protocol.XiaohongshuProtocolError):
            self.client.resolve_goods_bindings(["candy"])

    def test_null_data_refreshes_sso_once_and_recovers_catalog(self):
        self.session.post.side_effect = [
            response(payload={"success": True, "data": None}),
            response(payload={"success": True}),
            goods(),
        ]
        self.assertEqual(len(self.query()), 1)
        self.assertTrue(self.session.post.call_args_list[1].args[0].endswith("/service-ticket"))
        self.assertEqual(self.session.post.call_count, 3)

    def test_null_data_after_sso_is_not_reported_as_product_not_found(self):
        self.session.post.side_effect = [
            response(401, {"success": False, "code": -100}),
            response(payload={"success": True}),
            response(payload={"success": True, "data": None}),
        ]
        with self.assertRaisesRegex(protocol.XiaohongshuGoodsUnavailableError, "商品查询异常"):
            self.query()
        self.assertEqual(self.session.post.call_count, 3)
        self.sleep.assert_not_called()

    def test_null_data_and_missing_data_are_not_valid_empty_catalogs(self):
        for payload in [{"success": True}, {"success": True, "data": None},
                        {"success": True, "data": []}]:
            with self.subTest(payload=payload):
                with self.assertRaises(protocol.XiaohongshuGoodsUnavailableError):
                    protocol.extract_goods_list(payload)


if __name__ == "__main__":
    unittest.main()
