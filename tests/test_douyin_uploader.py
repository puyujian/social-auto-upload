import asyncio
import unittest
from unittest import mock

from uploader.douyin_uploader import main as douyin_main


class FakeLocator:
    def __init__(self, *, count=0, visible=False, enabled=True, on_click=None):
        self._count = count
        self._visible = visible
        self._enabled = enabled
        self._on_click = on_click
        self.clicks = 0

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._visible

    async def is_enabled(self):
        return self._enabled

    async def click(self):
        self.clicks += 1
        if self._on_click:
            self._on_click()

    async def set_input_files(self, _files):
        return None

    def locator(self, _selector):
        return FakeLocator()

    def get_by_text(self, _text, exact=False):
        return FakeLocator()


class DummyBrowser:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class DummyContext:
    def __init__(self):
        self.init_script_applied = False

    async def add_init_script(self, **kwargs):
        self.init_script_applied = True

    async def close(self):
        return None


class FakeFileChooser:
    def __init__(self, page):
        self.page = page

    async def set_files(self, files):
        self.page.file_chooser_files.append(files)


class FakeFileChooserContext:
    def __init__(self, page):
        self.page = page
        self.file_chooser = FakeFileChooser(page)

    @property
    def value(self):
        async def _value():
            return self.file_chooser

        return _value()

    async def __aenter__(self):
        self.page.file_chooser_armed = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.page.file_chooser_armed = False


class VideoUploadButtonLocator(FakeLocator):
    def __init__(self, page):
        super().__init__(count=1, visible=True, on_click=page._click_upload_button)


class VideoUploadInputLocator(FakeLocator):
    def __init__(self, page):
        super().__init__(count=1, visible=False)
        self.page = page

    async def wait_for(self, state="visible", timeout=None):
        self.page.input_waits.append((state, timeout))

    async def set_input_files(self, files):
        self.page.input_files.append(files)


class VideoUploadPage:
    def __init__(self, *, button_opens_file_chooser=True, guide_visible=False):
        self.button_opens_file_chooser = button_opens_file_chooser
        self.file_chooser_armed = False
        self.file_chooser_files = []
        self.input_files = []
        self.input_waits = []
        self.screenshots = []
        self.upload_button = VideoUploadButtonLocator(self)
        self.upload_input = VideoUploadInputLocator(self)
        self.guide_button = FakeLocator(count=1 if guide_visible else 0, visible=guide_visible, on_click=self._dismiss_guide)

    def _dismiss_guide(self):
        self.guide_button._count = 0
        self.guide_button._visible = False

    def _click_upload_button(self):
        if not self.button_opens_file_chooser or not self.file_chooser_armed:
            raise TimeoutError("file chooser did not open")

    def expect_file_chooser(self, timeout=5000):
        if not self.button_opens_file_chooser:
            raise TimeoutError("file chooser did not open")
        return FakeFileChooserContext(self)

    def get_by_text(self, text, exact=False):
        if text == douyin_main.DOUYIN_VIDEO_UPLOAD_BUTTON_TEXT:
            return self.upload_button
        if text in douyin_main.DOUYIN_UPLOAD_GUIDE_DISMISS_TEXTS:
            return self.guide_button
        return FakeLocator()

    def get_by_role(self, role, name=None, exact=False):
        if role == "button" and name in douyin_main.DOUYIN_UPLOAD_GUIDE_DISMISS_TEXTS:
            return self.guide_button
        return FakeLocator()

    def locator(self, selector):
        if selector == douyin_main.DOUYIN_VIDEO_UPLOAD_INPUT_SELECTOR:
            return self.upload_input
        return FakeLocator()

    async def screenshot(self, **kwargs):
        self.screenshots.append(kwargs)


class DouyinCloakContextTests(unittest.TestCase):
    def test_build_douyin_context_options_can_omit_storage_state_for_login(self):
        options = douyin_main.build_douyin_context_options()

        self.assertNotIn("storage_state", options)
        self.assertEqual(options["permissions"], ["geolocation"])

    def test_open_douyin_cloak_context_maps_options_to_cloakbrowser(self):
        captured = {}
        context = DummyContext()

        async def fake_launch_context_async(**kwargs):
            captured.update(kwargs)
            return context

        with mock.patch(
            "uploader.douyin_uploader.main._load_cloakbrowser_launch_context_async",
            return_value=fake_launch_context_async,
        ):
            browser, opened_context = asyncio.run(
                douyin_main.open_douyin_cloak_context(headless=True, storage_state="account.json")
            )

        self.assertIs(opened_context, context)
        self.assertIsInstance(browser, douyin_main._ClosedCloakBrowser)
        self.assertTrue(context.init_script_applied)
        self.assertEqual(captured["headless"], True)
        self.assertEqual(captured["backend"], "playwright")
        self.assertEqual(captured["storage_state"], "account.json")
        self.assertEqual(captured["permissions"], ["geolocation"])

    def test_video_open_publish_context_uses_cloakbrowser_adapter(self):
        app = douyin_main.DouYinVideo(
            title="title",
            file_path="video.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
            headless=False,
        )
        browser = DummyBrowser()
        context = DummyContext()

        async def fake_open_douyin_cloak_context(**kwargs):
            self.assertEqual(kwargs["headless"], False)
            self.assertEqual(kwargs["context_options"]["storage_state"], "account.json")
            self.assertEqual(kwargs["context_options"]["permissions"], ["geolocation"])
            return browser, context

        with mock.patch(
            "uploader.douyin_uploader.main.open_douyin_cloak_context",
            new=fake_open_douyin_cloak_context,
        ):
            opened_browser, opened_context = asyncio.run(app.open_publish_context())

        self.assertIs(opened_browser, browser)
        self.assertIs(opened_context, context)


class DeclarationSelectionModalLocator(FakeLocator):
    def __init__(self, page):
        super().__init__(
            count=page.selection_title._count,
            visible=page.selection_title._visible,
        )
        self.page = page

    def locator(self, selector):
        if "label.semi-radio" in selector and douyin_main.DOUYIN_DECLARATION_SELECTION_OPTION in selector:
            return self.page.selection_option
        if "button:has-text" in selector and douyin_main.DOUYIN_DECLARATION_CONFIRM_BUTTON in selector:
            return self.page.selection_confirm_button
        return FakeLocator()

    def get_by_text(self, text, exact=False):
        if text == douyin_main.DOUYIN_DECLARATION_SELECTION_OPTION:
            return self.page.selection_option
        return FakeLocator()


class AuthStatePage:
    def __init__(self, visible_texts):
        self.visible_texts = set(visible_texts)
        self.url = "https://creator.douyin.com/creator-micro/home"

    def get_by_text(self, text, exact=False):
        if text in self.visible_texts:
            return FakeLocator(count=1, visible=True)
        return FakeLocator()


class VideoPublishProbePage(AuthStatePage):
    def __init__(self, url, visible_texts=None):
        super().__init__(visible_texts or [])
        self.url = url
        self.screenshots = []

    async def screenshot(self, **kwargs):
        self.screenshots.append(kwargs)


class DeclarationModalPage:
    def __init__(self, *, modal_visible):
        count = 1 if modal_visible else 0
        self.title = FakeLocator(count=count, visible=modal_visible)
        self.direct_publish_button = FakeLocator(count=count, visible=modal_visible)

    def get_by_text(self, text, exact=False):
        if text == douyin_main.DOUYIN_DECLARATION_MODAL_TITLE:
            return self.title
        return FakeLocator()

    def get_by_role(self, role, name=None, exact=False):
        if role == "button" and name == douyin_main.DOUYIN_DECLARATION_DIRECT_PUBLISH_BUTTON:
            return self.direct_publish_button
        return FakeLocator()

    def locator(self, _selector):
        return FakeLocator()


class DeclarationSelectionModalPage:
    def __init__(self, *, modal_visible):
        count = 1 if modal_visible else 0
        self.title = FakeLocator(count=count, visible=modal_visible)
        self.selection_title = self.title
        self.option = FakeLocator(count=count, visible=modal_visible, on_click=self._select_option)
        self.selection_option = self.option
        self.confirm_button = FakeLocator(count=count, visible=modal_visible, enabled=False)
        self.selection_confirm_button = self.confirm_button

    def _select_option(self):
        self.confirm_button._enabled = True

    def get_by_text(self, text, exact=False):
        if text == douyin_main.DOUYIN_DECLARATION_SELECTION_MODAL_TITLE:
            return self.title
        if text == douyin_main.DOUYIN_DECLARATION_SELECTION_OPTION:
            return self.option
        return FakeLocator()

    def get_by_role(self, role, name=None, exact=False):
        if role == "button" and name == douyin_main.DOUYIN_DECLARATION_CONFIRM_BUTTON:
            return self.confirm_button
        return FakeLocator()

    def locator(self, selector):
        if douyin_main.DOUYIN_DECLARATION_SELECTION_MODAL_TITLE in selector:
            return DeclarationSelectionModalLocator(self)
        return FakeLocator()


class NotePublishPage:
    def __init__(self, *, modal_kind="direct"):
        self.modal_kind = modal_kind
        self.manage_ready = False
        self.manage_waits = 0
        self.title = FakeLocator()
        self.direct_publish_button = FakeLocator(on_click=self._confirm_declaration)
        self.selection_title = FakeLocator()
        self.selection_option = FakeLocator(on_click=self._select_declaration_option)
        self.selection_confirm_button = FakeLocator(enabled=False, on_click=self._confirm_declaration)
        self.publish_button = FakeLocator(count=1, visible=True, on_click=self._open_declaration_modal)
        self.note_tab = FakeLocator(count=1, visible=True)
        self.image_input = FakeLocator(count=1, visible=True)

    def _open_declaration_modal(self):
        if self.modal_kind == "selection":
            self.selection_title._count = 1
            self.selection_title._visible = True
            self.selection_option._count = 1
            self.selection_option._visible = True
            self.selection_confirm_button._count = 1
            self.selection_confirm_button._visible = True
            self.selection_confirm_button._enabled = False
        else:
            self.title._count = 1
            self.title._visible = True
            self.direct_publish_button._count = 1
            self.direct_publish_button._visible = True

    def _select_declaration_option(self):
        self.selection_confirm_button._enabled = True

    def _confirm_declaration(self):
        self.title._count = 0
        self.title._visible = False
        self.direct_publish_button._count = 0
        self.direct_publish_button._visible = False
        self.selection_title._count = 0
        self.selection_title._visible = False
        self.selection_option._count = 0
        self.selection_option._visible = False
        self.selection_confirm_button._count = 0
        self.selection_confirm_button._visible = False
        self.manage_ready = True

    def get_by_text(self, text, exact=False):
        if text == "发布图文":
            return self.note_tab
        if text == douyin_main.DOUYIN_DECLARATION_MODAL_TITLE:
            return self.title
        if text == douyin_main.DOUYIN_DECLARATION_SELECTION_MODAL_TITLE:
            return self.selection_title
        if text == douyin_main.DOUYIN_DECLARATION_SELECTION_OPTION:
            return self.selection_option
        return FakeLocator()

    def get_by_role(self, role, name=None, exact=False):
        if role == "button" and name == "发布":
            return self.publish_button
        if role == "button" and name == douyin_main.DOUYIN_DECLARATION_DIRECT_PUBLISH_BUTTON:
            return self.direct_publish_button
        if role == "button" and name == douyin_main.DOUYIN_DECLARATION_CONFIRM_BUTTON:
            return self.selection_confirm_button
        return FakeLocator()

    def locator(self, _selector):
        if douyin_main.DOUYIN_DECLARATION_SELECTION_MODAL_TITLE in _selector:
            return DeclarationSelectionModalLocator(self)
        return self.image_input

    async def wait_for_timeout(self, _timeout):
        return None

    async def wait_for_url(self, url, timeout=3000):
        if "post/image" in url:
            return None
        if "content/manage" in url:
            self.manage_waits += 1
            if self.manage_ready:
                return None
        raise TimeoutError("page did not navigate")


async def no_sleep(_delay):
    return None


class DouyinAuthStateTests(unittest.TestCase):
    def test_login_completion_rejects_loading_page_without_positive_markers(self):
        page = AuthStatePage([])

        completed = asyncio.run(douyin_main._is_douyin_login_completed(page))

        self.assertFalse(completed)

    def test_login_completion_rejects_visible_login_marker(self):
        page = AuthStatePage(["扫码登录", "点击上传"])

        completed = asyncio.run(douyin_main._is_douyin_login_completed(page))

        self.assertFalse(completed)

    def test_login_completion_accepts_authenticated_upload_marker(self):
        page = AuthStatePage(["点击上传"])

        completed = asyncio.run(douyin_main._is_douyin_login_completed(page))

        self.assertTrue(completed)

    def test_auth_state_reports_unknown_until_page_renders(self):
        page = AuthStatePage([])

        state = asyncio.run(douyin_main._douyin_auth_state(page))

        self.assertEqual(state, "unknown")


class DouyinVideoPublishPageWaitTests(unittest.TestCase):
    def test_accepts_version_2_publish_page(self):
        uploader = douyin_main.DouYinBaseUploader(0, "account.json")
        page = VideoPublishProbePage(
            "https://creator.douyin.com/creator-micro/content/post/video?enter_from=publish_page"
        )

        result = asyncio.run(uploader.wait_for_video_publish_page(page, timeout_seconds=1))

        self.assertEqual(result, "version_2")

    def test_rejects_login_redirect_without_waiting_for_global_timeout(self):
        uploader = douyin_main.DouYinBaseUploader(0, "account.json")
        page = VideoPublishProbePage(
            "https://creator.douyin.com/creator-micro/login",
            visible_texts=["扫码登录"],
        )

        with self.assertRaisesRegex(RuntimeError, "登录状态已失效"):
            asyncio.run(uploader.wait_for_video_publish_page(page, timeout_seconds=60))

        self.assertTrue(page.screenshots)

    def test_times_out_when_upload_page_never_enters_editor(self):
        uploader = douyin_main.DouYinBaseUploader(0, "account.json")
        page = VideoPublishProbePage(douyin_main.DOUYIN_UPLOAD_URL)

        with self.assertRaisesRegex(RuntimeError, "等待抖音视频发布编辑页超时"):
            asyncio.run(uploader.wait_for_video_publish_page(page, timeout_seconds=1))

        self.assertTrue(page.screenshots)


class DouyinVideoUploadInputTests(unittest.TestCase):
    def test_uploads_video_through_file_chooser_button(self):
        uploader = douyin_main.DouYinVideo(
            title="title",
            file_path="video.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
        )
        page = VideoUploadPage(button_opens_file_chooser=True)

        asyncio.run(uploader.upload_video_file_from_upload_page(page, guide_timeout_seconds=0))

        self.assertEqual(page.file_chooser_files, ["video.mp4"])
        self.assertEqual(page.input_files, [])

    def test_falls_back_to_video_input_when_button_does_not_open_file_chooser(self):
        uploader = douyin_main.DouYinVideo(
            title="title",
            file_path="video.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
        )
        page = VideoUploadPage(button_opens_file_chooser=False)

        asyncio.run(uploader.upload_video_file_from_upload_page(page, guide_timeout_seconds=0))

        self.assertEqual(page.file_chooser_files, [])
        self.assertEqual(page.input_files, ["video.mp4"])

    def test_dismisses_upload_page_guide_before_uploading(self):
        uploader = douyin_main.DouYinVideo(
            title="title",
            file_path="video.mp4",
            tags=[],
            publish_date=0,
            account_file="account.json",
        )
        page = VideoUploadPage(button_opens_file_chooser=True, guide_visible=True)

        asyncio.run(uploader.upload_video_file_from_upload_page(page, guide_timeout_seconds=0))

        self.assertEqual(page.guide_button.clicks, 1)
        self.assertEqual(page.file_chooser_files, ["video.mp4"])


class DouyinDeclarationModalTests(unittest.TestCase):
    def test_declaration_modal_present_clicks_direct_publish(self):
        uploader = douyin_main.DouYinBaseUploader(0, "account.json")
        page = DeclarationModalPage(modal_visible=True)

        handled = asyncio.run(uploader.handle_declaration_modal(page))

        self.assertTrue(handled)
        self.assertEqual(page.direct_publish_button.clicks, 1)

    def test_declaration_modal_absent_does_not_click(self):
        uploader = douyin_main.DouYinBaseUploader(0, "account.json")
        page = DeclarationModalPage(modal_visible=False)

        handled = asyncio.run(uploader.handle_declaration_modal(page))

        self.assertFalse(handled)
        self.assertEqual(page.direct_publish_button.clicks, 0)

    def test_declaration_selection_modal_selects_option_and_confirms(self):
        uploader = douyin_main.DouYinBaseUploader(0, "account.json")
        page = DeclarationSelectionModalPage(modal_visible=True)

        handled = asyncio.run(uploader.handle_declaration_modal(page))

        self.assertTrue(handled)
        self.assertEqual(page.option.clicks, 1)
        self.assertEqual(page.confirm_button.clicks, 1)

    def test_note_publish_waits_after_confirming_declaration_modal(self):
        uploader = douyin_main.DouYinNote(
            image_paths=["image.jpg"],
            note="note text",
            tags=[],
            publish_date=0,
            account_file="account.json",
            title="title",
        )
        page = NotePublishPage()

        async def fill_title_and_description(_page, _title, _description, _tags):
            return None

        uploader.fill_title_and_description = fill_title_and_description

        with mock.patch("uploader.douyin_uploader.main.asyncio.sleep", new=no_sleep):
            asyncio.run(uploader.upload_note_content(page))

        self.assertEqual(page.publish_button.clicks, 1)
        self.assertEqual(page.direct_publish_button.clicks, 1)
        self.assertEqual(page.manage_waits, 2)

    def test_note_publish_waits_after_confirming_declaration_selection_modal(self):
        uploader = douyin_main.DouYinNote(
            image_paths=["image.jpg"],
            note="note text",
            tags=[],
            publish_date=0,
            account_file="account.json",
            title="title",
        )
        page = NotePublishPage(modal_kind="selection")

        async def fill_title_and_description(_page, _title, _description, _tags):
            return None

        uploader.fill_title_and_description = fill_title_and_description

        with mock.patch("uploader.douyin_uploader.main.asyncio.sleep", new=no_sleep):
            asyncio.run(uploader.upload_note_content(page))

        self.assertEqual(page.publish_button.clicks, 1)
        self.assertEqual(page.selection_option.clicks, 1)
        self.assertEqual(page.selection_confirm_button.clicks, 1)
        self.assertEqual(page.manage_waits, 2)


if __name__ == "__main__":
    unittest.main()
