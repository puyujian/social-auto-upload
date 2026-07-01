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


class TextBodyLocator:
    def __init__(self, text):
        self.text = text

    async def inner_text(self, **_kwargs):
        return self.text


class BrokenDescriptionLocator:
    def locator(self, _selector):
        return self

    async def click(self):
        raise RuntimeError("locator timeout")


class ReadyDescriptionLocator:
    def __init__(self):
        self.clicked = False

    def locator(self, _selector):
        return self

    async def click(self):
        self.clicked = True


class PublishStatePage:
    def __init__(self, text="", *, description_locator=None):
        self.url = "https://cp.kuaishou.com/article/publish/video"
        self.text = text
        self.description_locator = description_locator or ReadyDescriptionLocator()
        self.screenshots = []

    def locator(self, selector):
        if selector == "body":
            return TextBodyLocator(self.text)
        raise AssertionError(f"unexpected locator: {selector}")

    def get_by_text(self, text, **_kwargs):
        if text == "描述":
            return self.description_locator
        raise AssertionError(f"unexpected text: {text}")

    async def screenshot(self, **kwargs):
        self.screenshots.append(kwargs)


class KuaishouCloakContextTests(unittest.TestCase):
    def test_build_kuaishou_context_options_can_omit_storage_state_for_login(self):
        options = ks_main.build_kuaishou_context_options()

        self.assertNotIn("storage_state", options)

    def test_build_kuaishou_context_options_merges_proxy(self):
        options = ks_main.build_kuaishou_context_options(
            "account.json",
            proxy={"server": "socks5h://proxy.local:1080"},
        )

        self.assertEqual(options["storage_state"], "account.json")
        self.assertEqual(options["proxy"], {"server": "socks5://proxy.local:1080"})

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
            proxy={"server": "http://127.0.0.1:7890"},
        )
        browser = DummyBrowser()
        context = DummyContext()

        async def fake_open_kuaishou_cloak_context(**kwargs):
            self.assertEqual(kwargs["headless"], False)
            self.assertEqual(kwargs["context_options"]["storage_state"], "account.json")
            self.assertEqual(kwargs["context_options"]["proxy"], {"server": "http://127.0.0.1:7890"})
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

    def test_classifies_publish_page_account_restricted_text(self):
        result = ks_main._classify_kuaishou_publish_page_state(
            url="https://cp.kuaishou.com/article/publish/video",
            visible_text="当前账号暂无发布权限，请处理违规处罚后再试",
        )

        self.assertEqual(result["error_code"], "kuaishou_publish_page_account_restricted")
        self.assertIn("发布权限受限", result["title"])

    def test_click_description_editor_raises_diagnostic_on_restricted_page(self):
        page = PublishStatePage("当前账号被处罚，暂无发布权限")
        app = ks_main.KSVideo(
            title="标题",
            file_path="video.mp4",
            tags=[],
            publish_date=0,
            account_file="D:/social-auto-upload/cookies/kuaishou_team.json",
        )

        with self.assertRaises(ks_main.KuaishouPublishPageStateError) as ctx:
            asyncio.run(app.click_description_editor(page))

        self.assertEqual(ctx.exception.error_code, "kuaishou_publish_page_account_restricted")
        self.assertTrue(page.screenshots)
        self.assertIn("现场截图", str(ctx.exception))

    def test_click_description_editor_reports_dom_change_when_locator_fails(self):
        page = PublishStatePage("发布视频 上传中 描述", description_locator=BrokenDescriptionLocator())
        app = ks_main.KSVideo(
            title="标题",
            file_path="video.mp4",
            tags=[],
            publish_date=0,
            account_file="D:/social-auto-upload/cookies/kuaishou_team.json",
        )

        with self.assertRaises(ks_main.KuaishouPublishPageStateError) as ctx:
            asyncio.run(app.click_description_editor(page))

        self.assertEqual(ctx.exception.error_code, "kuaishou_publish_page_dom_changed")
        self.assertIn("locator timeout", str(ctx.exception))
        self.assertTrue(page.screenshots)


if __name__ == "__main__":
    unittest.main()
