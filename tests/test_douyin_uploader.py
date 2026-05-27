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
