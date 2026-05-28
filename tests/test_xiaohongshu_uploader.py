import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import uploader.xiaohongshu_uploader.main as xhs_main


class FakeLocator:
    def __init__(self, name, count=0, src=None, children=None, attrs=None):
        self.name = name
        self._count = count
        self._src = src
        self._children = children or {}
        self._attrs = attrs or {}

    @property
    def first(self):
        return self

    def locator(self, selector):
        return self._children.get(selector, FakeLocator(selector))

    def get_by_text(self, text, exact=False):
        return self._children.get(f"text:{text}", FakeLocator(text))

    def filter(self, **kwargs):
        return self

    def nth(self, index):
        return self

    async def count(self):
        return self._count

    async def wait_for(self, **kwargs):
        return None

    async def get_attribute(self, name):
        if name in self._attrs:
            return self._attrs[name]
        if name == "src":
            return self._src
        return None

    async def fill(self, value):
        return None

    async def click(self, **kwargs):
        return None

    async def scroll_into_view_if_needed(self):
        return None

    async def bounding_box(self):
        return {"x": 0, "y": 0, "width": 240, "height": 48}

    async def is_visible(self):
        return True

    async def inner_text(self):
        return self.name


class RecordingKeyboard:
    def __init__(self):
        self.actions = []

    async def press(self, key):
        self.actions.append(("press", key))

    async def type(self, text, delay=None):
        self.actions.append(("type", text, delay))


class RecordingLocator(FakeLocator):
    def __init__(self, name, children=None, attrs=None):
        super().__init__(name, count=1, children=children, attrs=attrs)
        self.actions = []

    async def fill(self, value):
        self.actions.append(("fill", value))

    async def evaluate(self, script, value):
        self.actions.append(("evaluate", value))

    async def click(self, **kwargs):
        self.actions.append(("click", kwargs))

    async def wait_for(self, **kwargs):
        self.actions.append(("wait_for", kwargs))

    async def scroll_into_view_if_needed(self):
        self.actions.append(("scroll_into_view_if_needed",))

    async def bounding_box(self):
        return {"x": 0, "y": 0, "width": 240, "height": 48}

    async def inner_text(self):
        return self.name


class TimeoutLocator(RecordingLocator):
    async def wait_for(self, **kwargs):
        self.actions.append(("wait_for", kwargs))
        raise TimeoutError(f"{self.name} timed out")


class GroupOptionLocator(RecordingLocator):
    def __init__(self, name):
        super().__init__(name)
        self.container = RecordingLocator(f"{name}-container")

    def locator(self, selector):
        if selector.startswith("xpath=ancestor::"):
            return self.container
        return super().locator(selector)


class GroupDropdownLocator(FakeLocator):
    def __init__(self, option):
        super().__init__("dropdown", count=1)
        self.option = option

    def get_by_text(self, text, exact=False):
        return self.option

    def locator(self, selector):
        if selector.startswith("xpath=.//*"):
            return self.option
        return super().locator(selector)


class GroupChatPage:
    def __init__(self, group_name):
        self.keyboard = RecordingKeyboard()
        self.input = RecordingLocator("group-input")
        self.selector = RecordingLocator(group_name, children={"input": self.input})
        self.option = GroupOptionLocator(group_name)
        self.dropdown = GroupDropdownLocator(self.option)
        self.locators = {
            ".group-card-select": self.selector,
            "div.d-popover, div.d-dropdown": self.dropdown,
        }

    def locator(self, selector):
        return self.locators.get(selector, FakeLocator(selector))

    async def wait_for_timeout(self, timeout):
        return None

    def get_by_text(self, text, exact=False):
        return FakeLocator(str(text), count=0)


class RecordingPage:
    def __init__(self):
        self.keyboard = RecordingKeyboard()
        self.locators = {
            'input[placeholder*="填写标题"]': RecordingLocator("title"),
            'p[data-placeholder*="输入正文描述"]': RecordingLocator("desc"),
            '#creator-editor-topic-container': RecordingLocator("topic-container"),
            '#creator-editor-topic-container .item': RecordingLocator("topic-item"),
        }

    def locator(self, selector):
        return self.locators.get(selector, FakeLocator(selector))

    async def wait_for_timeout(self, timeout):
        return None


class PublishButtonPage:
    def __init__(self):
        self.keyboard = RecordingKeyboard()
        self.publish_button = RecordingLocator(
            "xhs-publish-btn",
            attrs={
                "is-save-draft": "true",
                "submit-disabled": "false",
            },
        )
        self.wait_functions = []

    def locator(self, selector):
        if selector == xhs_main.XHS_CUSTOM_PUBLISH_BUTTON_SELECTOR:
            return self.publish_button
        return FakeLocator(selector)

    async def wait_for_function(self, expression, arg=None, timeout=None):
        self.wait_functions.append((arg, timeout))
        return True


class RecordingHuman:
    def __init__(self):
        self.actions = []

    async def apply_fingerprint_obfuscation(self, page, context):
        self.actions.append(("fingerprint", page, context))

    async def goto(self, page, url):
        self.actions.append(("goto", url))

    async def click(self, page, selector=None, *, locator=None, timeout=None, **kwargs):
        target = locator.name if locator is not None else selector
        self.actions.append(("click", target, timeout, kwargs.get("position")))

    async def type(self, page, text, *, field_locator=None):
        self.actions.append(("type", text, field_locator.name if field_locator else None))


class DummyBrowser:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


class DummyContext:
    def __init__(self):
        self.init_script_applied = False
        self.closed = 0

    async def add_init_script(self, path):
        self.init_script_applied = True

    async def close(self):
        self.closed += 1


class XiaohongshuUploaderTests(unittest.TestCase):
    def test_find_xhs_qrcode_locator_prefers_scan_sibling_inside_login_box(self):
        qrcode_locator = FakeLocator("qrcode", count=1, src="data:image/png;base64,abc")
        scan_text_locator = FakeLocator(
            "scan-text",
            count=1,
            children={
                "xpath=..//following-sibling::div//img": qrcode_locator,
            },
        )
        login_box_locator = FakeLocator(
            "login-box",
            count=1,
            children={
                "div:has-text('扫一扫')": scan_text_locator,
                "text:APP扫一扫登录": scan_text_locator,
            },
        )
        page = FakeLocator(
            "page",
            children={
                "div[class*='login-box']": login_box_locator,
                ".login-box-container": login_box_locator,
            },
        )

        locator = asyncio.run(xhs_main._find_xhs_qrcode_locator(page))
        self.assertIs(locator, qrcode_locator)

    def test_setup_returns_detail_when_cookie_invalid_without_handle(self):
        with patch("uploader.xiaohongshu_uploader.main.os.path.exists", return_value=False):
            result = asyncio.run(
                xhs_main.xiaohongshu_setup(
                    "missing.json",
                    handle=False,
                    return_detail=True,
                )
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "cookie_invalid")

    def test_setup_uses_login_flow_when_handle_is_true(self):
        login_result = {
            "success": True,
            "status": "success",
            "message": "ok",
            "account_file": "account.json",
            "qrcode": {"image_path": "qrcode.png"},
            "current_url": "https://creator.xiaohongshu.com/",
        }
        with patch("uploader.xiaohongshu_uploader.main.os.path.exists", return_value=False):
            with patch(
                "uploader.xiaohongshu_uploader.main.xiaohongshu_cookie_gen",
                new=AsyncMock(return_value=login_result),
            ) as mock_login:
                result = asyncio.run(
                    xhs_main.xiaohongshu_setup(
                        "account.json",
                        handle=True,
                        return_detail=True,
                    )
                )
        self.assertTrue(result["success"])
        mock_login.assert_awaited_once()

    def test_video_validate_upload_args_normalizes_video_and_thumbnail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            thumbnail_path = Path(tmp_dir) / "demo.png"
            cookie_path = Path(tmp_dir) / "account.json"
            video_path.write_bytes(b"video")
            thumbnail_path.write_bytes(b"image")
            cookie_path.write_text("{}")

            app = xhs_main.XiaoHongShuVideo(
                title="demo",
                file_path=str(video_path),
                tags=["xhs"],
                publish_date=0,
                account_file=str(cookie_path),
                thumbnail_path=str(thumbnail_path),
            )

            with patch(
                "uploader.xiaohongshu_uploader.main.cookie_auth",
                new=AsyncMock(return_value=True),
            ):
                asyncio.run(app.validate_upload_args())

        self.assertTrue(app.file_path.endswith("demo.mp4"))
        self.assertTrue(app.thumbnail_path.endswith("demo.png"))

    def test_note_uploader_exists_and_validates_required_fields(self):
        note_cls = getattr(xhs_main, "XiaoHongShuNote")
        app = note_cls(
            image_paths=[],
            note="",
            tags=[],
            publish_date=0,
            account_file="account.json",
        )

        with patch.object(app, "validate_base_args", new=AsyncMock(return_value=None)):
            with self.assertRaises(ValueError):
                asyncio.run(app.validate_upload_args())

    def test_video_fill_meta_uses_desc_then_all_tags(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=["话题1", "话题2", "#话题3", ""],
            publish_date=0,
            account_file="account.json",
            desc="描述内容",
        )
        app.human = RecordingHuman()
        page = RecordingPage()

        asyncio.run(app.fill_meta(page))

        self.assertEqual(
            page.locators['input[placeholder*="填写标题"]'].actions,
            [
                ("wait_for", {"state": "visible", "timeout": 10000}),
                ("scroll_into_view_if_needed",),
            ],
        )
        self.assertIn(("type", "标题内容", None), app.human.actions)
        self.assertEqual(
            page.locators['p[data-placeholder*="输入正文描述"]'].actions,
            [
                ("wait_for", {"state": "visible", "timeout": 10000}),
                ("scroll_into_view_if_needed",),
            ],
        )
        self.assertIn(("click", "title", 10000, None), app.human.actions)
        self.assertIn(("click", "desc", 10000, {"x": 19.2, "y": 24.0}), app.human.actions)
        self.assertIn(("type", "描述内容", None), app.human.actions)
        self.assertEqual(page.keyboard.actions[:5], [
            ("press", "Control+A"),
            ("press", "Backspace"),
            ("press", "Control+A"),
            ("press", "Backspace"),
            ("press", "Enter"),
        ])
        self.assertIn(("type", "#话题1", None), app.human.actions)
        self.assertIn(("type", "#话题2", None), app.human.actions)
        self.assertIn(("type", "#话题3", None), app.human.actions)
        self.assertEqual(
            page.locators['#creator-editor-topic-container .item'].actions,
            [
                ("wait_for", {"state": "visible", "timeout": 5000}),
                ("scroll_into_view_if_needed",),
                ("wait_for", {"state": "visible", "timeout": 5000}),
                ("scroll_into_view_if_needed",),
                ("wait_for", {"state": "visible", "timeout": 5000}),
                ("scroll_into_view_if_needed",),
            ],
        )
        self.assertIn(("click", "topic-item", 10000, None), app.human.actions)

    def test_video_fill_meta_can_fill_first_tag_without_desc(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=["话题1"],
            publish_date=0,
            account_file="account.json",
        )
        app.human = RecordingHuman()
        page = RecordingPage()

        asyncio.run(app.fill_meta(page))

        self.assertEqual(
            page.locators['p[data-placeholder*="输入正文描述"]'].actions,
            [
                ("wait_for", {"state": "visible", "timeout": 10000}),
                ("scroll_into_view_if_needed",),
            ],
        )
        self.assertIn(("click", "desc", 10000, {"x": 19.2, "y": 24.0}), app.human.actions)
        self.assertNotIn(("type", "", None), app.human.actions)
        self.assertIn(("type", "#话题1", None), app.human.actions)

    def test_fill_desc_uses_manual_clear_and_typing(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
            desc="描述内容",
        )
        app.human = RecordingHuman()
        page = RecordingPage()

        asyncio.run(app.fill_desc(page))

        self.assertEqual(
            page.locators['p[data-placeholder*="输入正文描述"]'].actions,
            [
                ("wait_for", {"state": "visible", "timeout": 10000}),
                ("scroll_into_view_if_needed",),
            ],
        )
        self.assertIn(("click", "desc", 10000, {"x": 19.2, "y": 24.0}), app.human.actions)
        self.assertIn(("type", "描述内容", None), app.human.actions)
        self.assertNotIn(("press", "Control+KeyA"), page.keyboard.actions)
        self.assertIn(("press", "Control+A"), page.keyboard.actions)
        self.assertNotIn(("press", "Delete"), page.keyboard.actions)
        self.assertIn(("press", "Backspace"), page.keyboard.actions)
        self.assertIn(("press", "Enter"), page.keyboard.actions)

    def test_set_schedule_time_uses_manual_clear_and_typing(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
        )
        app.human = RecordingHuman()
        page = RecordingPage()
        schedule_switch = RecordingLocator("schedule-switch")
        page.locators[".custom-switch-card"] = RecordingLocator(
            "schedule-card",
            children={".d-switch": schedule_switch},
        )
        page.locators[".d-datepicker-input-filter input.d-text"] = RecordingLocator("date-input")

        with patch("uploader.xiaohongshu_uploader.main.asyncio.sleep", new=AsyncMock(return_value=None)):
            asyncio.run(app.set_schedule_time_xiaohongshu(page, datetime(2026, 5, 4, 9, 30)))

        self.assertIn(("click", "schedule-switch", 10000, None), app.human.actions)
        self.assertEqual(
            page.locators[".d-datepicker-input-filter input.d-text"].actions,
            [
                ("wait_for", {"state": "visible", "timeout": 10000}),
                ("scroll_into_view_if_needed",),
            ],
        )
        self.assertIn(("type", "2026-05-04 09:30", None), app.human.actions)
        self.assertNotIn(("press", "Control+KeyA"), page.keyboard.actions)
        self.assertIn(("press", "Control+A"), page.keyboard.actions)
        self.assertNotIn(("press", "Delete"), page.keyboard.actions)
        self.assertIn(("press", "Backspace"), page.keyboard.actions)

    def test_video_fill_meta_keeps_text_tag_when_topic_suggestion_times_out(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=["话题1"],
            publish_date=0,
            account_file="account.json",
        )
        app.human = RecordingHuman()
        page = RecordingPage()
        page.locators["#creator-editor-topic-container"] = TimeoutLocator("topic-container")

        asyncio.run(app.fill_meta(page))

        self.assertIn(("type", "#话题1", None), app.human.actions)
        self.assertIn(("press", "Enter"), page.keyboard.actions)
        self.assertEqual(page.locators['#creator-editor-topic-container .item'].actions, [])

    def test_video_keeps_optional_group_chat_name(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
            group_chat="手作交流群",
        )

        self.assertEqual(app.group_chat, "手作交流群")

    def test_set_group_chat_clicks_visible_dropdown_option_container(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
            group_chat="手作交流群",
        )
        app.human = RecordingHuman()
        page = GroupChatPage("手作交流群")

        asyncio.run(app.set_group_chat(page, "手作交流群"))

        self.assertEqual(
            page.input.actions,
            [
                ("wait_for", {"state": "visible", "timeout": 10000}),
                ("scroll_into_view_if_needed",),
            ],
        )
        self.assertIn(("click", "手作交流群", 10000, None), app.human.actions)
        self.assertIn(("click", "group-input", 10000, None), app.human.actions)
        self.assertIn(("type", "手作交流群", None), app.human.actions)
        self.assertIn(("press", "Control+A"), page.keyboard.actions)
        self.assertIn(("press", "Backspace"), page.keyboard.actions)
        self.assertIn(("click", "手作交流群-container", 10000, None), app.human.actions)
        self.assertNotIn(("click",), page.option.actions)

    def test_apply_human_behavior_uses_page_and_context(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
        )
        app.human = RecordingHuman()
        page = object()
        context = object()

        asyncio.run(app.apply_human_behavior(page, context))

        self.assertEqual(app.human.actions, [])

    def test_build_browser_context_options_uses_stable_profile(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
        )

        options = app.build_browser_context_options()

        self.assertEqual(options["storage_state"], "account.json")
        self.assertEqual(options["viewport"], {"width": 1536, "height": 864})
        self.assertEqual(options["locale"], "zh-CN")
        self.assertEqual(options["timezone_id"], "Asia/Shanghai")
        self.assertEqual(options["permissions"], ["geolocation"])

    def test_validate_base_args_skips_cookie_probe_when_cookie_verified(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
            cookie_verified=True,
        )

        with patch("uploader.xiaohongshu_uploader.main.os.path.exists", return_value=True):
            with patch("uploader.xiaohongshu_uploader.main.cookie_auth", new=AsyncMock()) as mock_cookie_auth:
                asyncio.run(app.validate_base_args())

        mock_cookie_auth.assert_not_awaited()

    def test_build_xhs_context_options_can_omit_storage_state_for_login(self):
        options = xhs_main.build_xhs_context_options()

        self.assertNotIn("storage_state", options)
        self.assertEqual(options["viewport"], {"width": 1536, "height": 864})
        self.assertEqual(options["locale"], "zh-CN")
        self.assertEqual(options["timezone_id"], "Asia/Shanghai")

    def test_open_xhs_cloak_context_maps_stable_options_to_cloakbrowser(self):
        captured = {}
        context = DummyContext()

        async def fake_launch_context_async(**kwargs):
            captured.update(kwargs)
            return context

        with patch(
            "uploader.xiaohongshu_uploader.main._load_cloakbrowser_launch_context_async",
            return_value=fake_launch_context_async,
        ):
            browser, opened_context = asyncio.run(
                xhs_main.open_xhs_cloak_context(headless=False, storage_state="account.json")
            )

        self.assertIs(opened_context, context)
        self.assertIsInstance(browser, xhs_main._ClosedCloakBrowser)
        self.assertTrue(context.init_script_applied)
        self.assertEqual(captured["headless"], False)
        self.assertEqual(captured["backend"], "playwright")
        self.assertEqual(captured["storage_state"], "account.json")
        self.assertEqual(captured["viewport"], {"width": 1536, "height": 864})
        self.assertEqual(captured["locale"], "zh-CN")
        self.assertEqual(captured["timezone"], "Asia/Shanghai")
        self.assertEqual(captured["permissions"], ["geolocation"])

    def test_open_publish_context_uses_cloakbrowser_adapter(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
        )
        browser = DummyBrowser()
        context = DummyContext()

        async def fake_open_xhs_cloak_context(**kwargs):
            self.assertEqual(kwargs["headless"], False)
            self.assertEqual(kwargs["context_options"]["storage_state"], "account.json")
            return browser, context

        with patch(
            "uploader.xiaohongshu_uploader.main.open_xhs_cloak_context",
            new=fake_open_xhs_cloak_context,
        ):
            opened_browser, opened_context = asyncio.run(app.open_publish_context())

        self.assertIs(opened_browser, browser)
        self.assertIs(opened_context, context)

    def test_click_publish_submit_uses_xhs_custom_publish_button(self):
        app = xhs_main.XiaoHongShuVideo(
            title="标题内容",
            file_path="demo.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
        )
        app.human = RecordingHuman()
        page = PublishButtonPage()

        asyncio.run(app.click_publish_submit(page, "发布"))

        self.assertEqual(
            page.wait_functions,
            [(xhs_main.XHS_CUSTOM_PUBLISH_BUTTON_SELECTOR, xhs_main.XHS_PUBLISH_BUTTON_READY_TIMEOUT)],
        )
        click_action = next(action for action in app.human.actions if action[0] == "click")
        self.assertEqual(click_action[1], "xhs-publish-btn")
        self.assertEqual(click_action[2], 30000)
        self.assertAlmostEqual(click_action[3]["x"], 146.4)
        self.assertAlmostEqual(click_action[3]["y"], 24.0)

    def test_note_title_defaults_do_not_override_explicit_title(self):
        app = xhs_main.XiaoHongShuNote(
            image_paths=["a.png"],
            note="正文",
            tags=[],
            publish_date=0,
            account_file="account.json",
            title="显式标题",
            desc="图文正文",
        )

        self.assertEqual(app.title, "显式标题")
        self.assertEqual(app.desc, "图文正文")


if __name__ == "__main__":
    unittest.main()
