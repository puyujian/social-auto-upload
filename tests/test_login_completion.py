import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from uploader.xiaohongshu_uploader import main as xhs


class LoginCompletionTests(unittest.TestCase):
    def test_completed_login_saves_cookie_without_opening_second_browser(self):
        async def run():
            page = SimpleNamespace(url=xhs.XHS_HOME_LOGIN_URL, goto=AsyncMock())
            context = SimpleNamespace(new_page=AsyncMock(return_value=page), storage_state=AsyncMock(), close=AsyncMock())
            browser = SimpleNamespace(close=AsyncMock())
            with tempfile.TemporaryDirectory() as temp:
                account_file = str(Path(temp) / "account.json")
                qr = str(Path(temp) / "qr.png")
                with patch.object(xhs, "open_xhs_cloak_context", new=AsyncMock(return_value=(browser, context))) as launch, \
                     patch.object(xhs, "_save_xhs_qrcode", new=AsyncMock(return_value={"image_path": qr})), \
                     patch.object(xhs, "_is_xhs_login_completed", new=AsyncMock(return_value=True)), \
                     patch.object(xhs, "cookie_auth", new=AsyncMock(side_effect=RuntimeError("must not launch another browser"))) as auth, \
                     patch.object(xhs.asyncio, "sleep", new=AsyncMock()):
                    result = await xhs.xiaohongshu_cookie_gen(account_file, max_checks=1, headless=True)
                self.assertTrue(result["success"])
                context.storage_state.assert_awaited_once_with(path=account_file)
                launch.assert_awaited_once()
                auth.assert_not_awaited()
                context.close.assert_awaited_once()
        asyncio.run(run())

    def test_unconfirmed_scan_never_saves_cookie_or_reports_success(self):
        async def run():
            page = SimpleNamespace(url=xhs.XHS_HOME_LOGIN_URL, goto=AsyncMock())
            context = SimpleNamespace(new_page=AsyncMock(return_value=page), storage_state=AsyncMock(), close=AsyncMock())
            browser = SimpleNamespace(close=AsyncMock())
            with tempfile.TemporaryDirectory() as temp:
                account_file = str(Path(temp) / "account.json")
                with patch.object(xhs, "open_xhs_cloak_context", new=AsyncMock(return_value=(browser, context))), \
                     patch.object(xhs, "_save_xhs_qrcode", new=AsyncMock(return_value={"image_path": str(Path(temp) / "qr.png")})), \
                     patch.object(xhs, "_is_xhs_login_completed", new=AsyncMock(return_value=False)), \
                     patch.object(xhs.asyncio, "sleep", new=AsyncMock()):
                    result = await xhs.xiaohongshu_cookie_gen(account_file, max_checks=1, headless=True)
                self.assertFalse(result["success"])
                self.assertEqual(result["status"], "timeout")
                context.storage_state.assert_not_awaited()
                context.close.assert_awaited_once()
        asyncio.run(run())
