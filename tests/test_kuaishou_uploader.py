import asyncio
import unittest
from unittest.mock import patch

from uploader.ks_uploader import main as ks_main


class DummyBrowser:
    async def close(self):
        return None


class DummyContext:
    def __init__(self):
        self.init_script_applied = False

    async def add_init_script(self, **kwargs):
        self.init_script_applied = True

    async def close(self):
        return None


class GotoTimeoutPage:
    async def goto(self, *_args, **_kwargs):
        raise TimeoutError("Page.goto: Timeout 30000ms exceeded.")


class GotoTimeoutContext(DummyContext):
    async def new_page(self):
        return GotoTimeoutPage()


class KuaishouCloakContextTests(unittest.TestCase):
    def test_build_kuaishou_context_options_can_omit_storage_state_for_login(self):
        options = ks_main.build_kuaishou_context_options()

        self.assertNotIn("storage_state", options)

    def test_open_kuaishou_cloak_context_maps_options_to_cloakbrowser(self):
        captured = {}
        context = DummyContext()

        async def fake_launch_context_async(**kwargs):
            captured.update(kwargs)
            return context

        with patch(
            "uploader.ks_uploader.main._load_cloakbrowser_launch_context_async",
            return_value=fake_launch_context_async,
        ):
            browser, opened_context = asyncio.run(
                ks_main.open_kuaishou_cloak_context(headless=True, storage_state="account.json")
            )

        self.assertIs(opened_context, context)
        self.assertIsInstance(browser, ks_main._ClosedCloakBrowser)
        self.assertTrue(context.init_script_applied)
        self.assertEqual(captured["headless"], True)
        self.assertEqual(captured["backend"], "playwright")
        self.assertEqual(captured["storage_state"], "account.json")

    def test_video_open_publish_context_uses_cloakbrowser_adapter(self):
        app = ks_main.KSVideo(
            title="标题",
            file_path="video.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
            headless=False,
        )
        browser = DummyBrowser()
        context = DummyContext()

        async def fake_open_kuaishou_cloak_context(**kwargs):
            self.assertEqual(kwargs["headless"], False)
            self.assertEqual(kwargs["context_options"]["storage_state"], "account.json")
            return browser, context

        with patch(
            "uploader.ks_uploader.main.open_kuaishou_cloak_context",
            new=fake_open_kuaishou_cloak_context,
        ):
            opened_browser, opened_context = asyncio.run(app.open_publish_context())

        self.assertIs(opened_browser, browser)
        self.assertIs(opened_context, context)

    def test_cookie_auth_raises_check_error_when_publish_page_probe_times_out(self):
        async def fake_open_kuaishou_cloak_context(**_kwargs):
            return DummyBrowser(), GotoTimeoutContext()

        with patch(
            "uploader.ks_uploader.main.open_kuaishou_cloak_context",
            new=fake_open_kuaishou_cloak_context,
        ):
            with self.assertRaisesRegex(ks_main.KuaishouCookieCheckError, "无法确认登录态"):
                asyncio.run(ks_main.cookie_auth("account.json"))

    def test_setup_returns_check_failed_detail_without_treating_probe_error_as_expired(self):
        with patch("uploader.ks_uploader.main.os.path.exists", return_value=True):
            with patch(
                "uploader.ks_uploader.main.cookie_auth",
                side_effect=ks_main.KuaishouCookieCheckError("探测失败"),
            ):
                result = asyncio.run(
                    ks_main.ks_setup("account.json", handle=False, return_detail=True)
                )

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "cookie_check_failed")
        self.assertEqual(result["message"], "探测失败")


if __name__ == "__main__":
    unittest.main()
