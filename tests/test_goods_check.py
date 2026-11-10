import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import sau_cli
from uploader.xiaohongshu_uploader.protocol import XiaohongshuGoodsUnavailableError


class GoodsCheckTests(unittest.TestCase):
    def test_check_accepts_products_without_enabling_publish(self):
        args = sau_cli.build_parser().parse_args([
            "xiaohongshu", "check", "--account", "test",
            "--publish-mode", "protocol", "--products", "candy,tea",
        ])
        self.assertEqual(args.products, "candy,tea")
        self.assertFalse(getattr(args, "confirm_protocol_publish", False))

    def test_upload_permit_alone_does_not_pass_goods_check(self):
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / "account.json"
            cookie.write_text("{}")
            client = Mock()
            client.resolve_goods_bindings.side_effect = XiaohongshuGoodsUnavailableError("商品查询异常")
            with patch.object(sau_cli, "resolve_account_file", return_value=cookie), patch.object(
                sau_cli, "XhsCreatorProtocol", return_value=client,
            ):
                with self.assertRaises(XiaohongshuGoodsUnavailableError):
                    asyncio.run(sau_cli.check_xiaohongshu_protocol_account("test", ["candy"]))
            client.validate_login_state.assert_called_once()
            client.resolve_goods_bindings.assert_called_once_with(["candy"])
            client.publish_note.assert_not_called()
