import asyncio
import unittest

from uploader.douyin_uploader import main as douyin_main


class FakeLocator:
    def __init__(self, *, count=0, visible=False):
        self._count = count
        self._visible = visible

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._visible


class AuthStatePage:
    def __init__(self, visible_texts):
        self.visible_texts = set(visible_texts)
        self.url = "https://creator.douyin.com/creator-micro/home"

    def get_by_text(self, text, exact=False):
        if text in self.visible_texts:
            return FakeLocator(count=1, visible=True)
        return FakeLocator()


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


if __name__ == "__main__":
    unittest.main()
