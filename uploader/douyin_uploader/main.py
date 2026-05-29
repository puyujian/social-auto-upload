# -*- coding: utf-8 -*-
from datetime import datetime

import asyncio
import inspect
import os
from pathlib import Path
from typing import Any
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from conf import DEBUG_MODE, LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH
from uploader.base_video import BaseVideoUploader
from utils.base_social_media import set_init_script
from utils.login_qrcode import build_login_qrcode_path
from utils.login_qrcode import decode_qrcode_from_path
from utils.login_qrcode import print_terminal_qrcode
from utils.login_qrcode import remove_qrcode_file
from utils.login_qrcode import save_data_url_image
from utils.log import douyin_logger

if TYPE_CHECKING:
    from playwright.async_api import Page
else:
    Page = Any

DOUYIN_PUBLISH_STRATEGY_IMMEDIATE = "immediate"
DOUYIN_PUBLISH_STRATEGY_SCHEDULED = "scheduled"
DOUYIN_UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
DOUYIN_AUTHENTICATED_TEXTS = ("点击上传", "上传视频", "发布图文", "内容管理", "作品管理")
DOUYIN_LOGIN_TEXTS = ("扫码登录", "验证码登录", "密码登录", "登录/注册")
DOUYIN_DECLARATION_MODAL_TITLE = "未添加自主声明"
DOUYIN_DECLARATION_DIRECT_PUBLISH_BUTTON = "直接发布"
DOUYIN_DECLARATION_SELECTION_MODAL_TITLE = "对作品内容添加声明"
DOUYIN_DECLARATION_SELECTION_OPTION = "无需添加自主声明"
DOUYIN_DECLARATION_CONFIRM_BUTTON = "确定"
DOUYIN_MODAL_SELECTOR = ".semi-modal-content"
DOUYIN_VIDEO_PUBLISH_PAGE_TIMEOUT_SECONDS = 180
DOUYIN_VIDEO_UPLOAD_INPUT_SELECTOR = (
    'input[type="file"][accept*="video"], '
    'input[type="file"][accept*=".mp4"], '
    'input[type="file"][accept*=".webm"]'
)
DOUYIN_VIDEO_UPLOAD_BUTTON_TEXT = "上传视频"
DOUYIN_UPLOAD_GUIDE_DISMISS_TEXTS = ("我知道了", "知道了")
DOUYIN_VIDEO_PUBLISH_URL_MARKERS = (
    "/creator-micro/content/publish",
    "/creator-micro/content/post/video",
)
DOUYIN_CLOAKBROWSER_BACKEND = "playwright"
DOUYIN_STABLE_CONTEXT_OPTIONS = {
    "permissions": ["geolocation"],
}


def _msg(emoji: str, text: str) -> str:
    return f"{emoji} {text}"


def build_douyin_context_options(storage_state: str | os.PathLike | None = None) -> dict:
    options = dict(DOUYIN_STABLE_CONTEXT_OPTIONS)
    if storage_state:
        options["storage_state"] = str(storage_state)
    return options


class _ClosedCloakBrowser:
    """兼容旧代码里 browser/context 分开关闭的顺序。"""

    async def close(self) -> None:
        return None


def _load_cloakbrowser_launch_context_async():
    try:
        from cloakbrowser import launch_context_async
    except ModuleNotFoundError as exc:
        if exc.name == "cloakbrowser":
            raise RuntimeError(
                "抖音链路现在使用 CloakBrowser，请先安装依赖: uv pip install -e ."
            ) from exc
        raise
    return launch_context_async


async def open_douyin_cloak_context(
    *,
    headless: bool,
    storage_state: str | os.PathLike | None = None,
    context_options: dict[str, Any] | None = None,
):
    """使用 CloakBrowser 打开抖音上下文，并保留当前 cookie 与权限参数。"""
    launch_context_async = _load_cloakbrowser_launch_context_async()
    options = dict(context_options or build_douyin_context_options(storage_state))

    # 当前链路依赖 add_init_script 注入统一脚本；固定 Playwright backend 避免 Patchright backend 兼容风险。
    context = await launch_context_async(
        headless=headless,
        backend=DOUYIN_CLOAKBROWSER_BACKEND,
        **options,
    )
    context = await set_init_script(context)
    return _ClosedCloakBrowser(), context


async def _emit_qrcode_callback(qrcode_callback, payload: dict):
    if not qrcode_callback:
        return

    callback_result = qrcode_callback(payload)
    if inspect.isawaitable(callback_result):
        await callback_result


def _build_login_result(success: bool, status: str, message: str, account_file: str, qrcode: dict | None = None, current_url: str = "") -> dict:
    return {
        "success": success,
        "status": status,
        "message": message,
        "account_file": str(account_file),
        "qrcode": qrcode,
        "current_url": current_url,
    }


async def _has_visible_text(page: Page, text: str) -> bool:
    locator = page.get_by_text(text, exact=False).first
    if not await locator.count():
        return False
    try:
        return await locator.is_visible()
    except Exception:
        return False


async def _is_visible_locator(locator) -> bool:
    locator = locator.first
    try:
        if not await locator.count():
            return False
        return await locator.is_visible()
    except Exception:
        return False


async def _is_enabled_locator(locator) -> bool:
    locator = locator.first
    try:
        if not await locator.count():
            return False
        if not await locator.is_visible():
            return False
        return await locator.is_enabled()
    except Exception:
        return False


async def _douyin_auth_state(page: Page) -> str:
    for text in DOUYIN_LOGIN_TEXTS:
        if await _has_visible_text(page, text):
            return "login"
    for text in DOUYIN_AUTHENTICATED_TEXTS:
        if await _has_visible_text(page, text):
            return "authenticated"
    return "unknown"


async def _wait_for_douyin_auth_state(page: Page, timeout: int = 15000, poll_interval: float = 0.5) -> str:
    deadline = asyncio.get_running_loop().time() + timeout / 1000
    state = await _douyin_auth_state(page)
    while state == "unknown" and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(poll_interval)
        state = await _douyin_auth_state(page)
    return state


def _is_douyin_video_publish_page_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    return parsed.netloc.endswith("creator.douyin.com") and parsed.path in DOUYIN_VIDEO_PUBLISH_URL_MARKERS


def _is_douyin_login_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    return parsed.netloc.endswith("creator.douyin.com") and "login" in parsed.path.lower()


async def _save_failure_screenshot(page: Page, account_file: str | os.PathLike, prefix: str) -> str:
    try:
        account_path = Path(account_file).expanduser()
        if account_path.parent.name == "cookies":
            log_dir = account_path.parent.parent / "logs"
        else:
            log_dir = Path.cwd() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = log_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        return str(screenshot_path.resolve())
    except Exception as exc:
        return f"截图保存失败: {exc}"


async def cookie_auth(account_file):
    browser, context = await open_douyin_cloak_context(headless=True, storage_state=account_file)
    try:
        page = await context.new_page()
        await page.goto(DOUYIN_UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
        return await _wait_for_douyin_auth_state(page) == "authenticated"
    finally:
        await context.close()
        await browser.close()


async def douyin_setup(account_file, handle=False, return_detail=False, qrcode_callback=None, headless: bool = LOCAL_CHROME_HEADLESS):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            result = _build_login_result(False, "cookie_invalid", "cookie文件不存在或已失效", account_file)
            return result if return_detail else False
        douyin_logger.info(_msg("🥹", "cookie 失效了，准备打开浏览器重新登录"))
        result = await douyin_cookie_gen(account_file, qrcode_callback=qrcode_callback, headless=headless)
        return result if return_detail else result["success"]

    result = _build_login_result(True, "cookie_valid", "cookie有效", account_file)
    return result if return_detail else True


async def _extract_douyin_qrcode_src(page: Page) -> str:
    scan_login_tab = page.get_by_text("扫码登录", exact=True).first
    await scan_login_tab.wait_for(timeout=30000)

    qrcode_img = (
        scan_login_tab
        .locator("..")
        .locator("xpath=following-sibling::div[1]")
        .locator('img[aria-label="二维码"]')
        .first
    )

    if not await qrcode_img.count():
        qrcode_img = page.get_by_role("img", name="二维码").first

    await qrcode_img.wait_for(state="visible", timeout=30000)
    src = await qrcode_img.get_attribute("src")
    if not src:
        raise RuntimeError("未获取到抖音登录二维码地址")

    return src


async def _save_douyin_qrcode(page: Page, account_file: str, previous_qrcode_path: Path | None = None, qrcode_callback=None) -> dict:
    qrcode_src = await _extract_douyin_qrcode_src(page)
    qrcode_path = save_data_url_image(qrcode_src, build_login_qrcode_path(account_file))
    if previous_qrcode_path and previous_qrcode_path != qrcode_path:
        if remove_qrcode_file(previous_qrcode_path):
            douyin_logger.info(_msg("🧹", f"临时二维码文件已清理: {previous_qrcode_path}"))
    douyin_logger.info(_msg("🖼️", f"二维码已经准备好啦，已保存到: {qrcode_path}"))
    qrcode_content = decode_qrcode_from_path(qrcode_path)
    if qrcode_content:
        print_terminal_qrcode(qrcode_content, qrcode_path, "抖音APP")
    else:
        douyin_logger.warning(_msg("😵", f"终端没法完整显示二维码，请打开 {qrcode_path} 扫码"))
    qrcode_info = {
        "image_path": str(qrcode_path),
        "image_data_url": qrcode_src,
    }
    await _emit_qrcode_callback(qrcode_callback, qrcode_info)
    return qrcode_info


async def _is_douyin_login_completed(page: Page) -> bool:
    return await _douyin_auth_state(page) == "authenticated"


async def _wait_for_douyin_login(page: Page, account_file: str, qrcode_info: dict, qrcode_callback=None, poll_interval: int = 3, max_checks: int = 100) -> dict:
    qrcode_path = Path(qrcode_info["image_path"])
    for _ in range(max_checks):
        if await _is_douyin_login_completed(page):
            douyin_logger.info(_msg("🥳", f"扫码成功，已经跳转到登录后页面: {page.url}"))
            return _build_login_result(True, "success", "抖音扫码登录成功", account_file, qrcode_info, page.url)

        expired_box = page.get_by_text("二维码失效", exact=True).locator("..").first
        if await expired_box.count() and await expired_box.is_visible():
            douyin_logger.warning(_msg("😵", "二维码失效了，小人马上去刷新"))
            await expired_box.click()
            await asyncio.sleep(1)
            qrcode_info = await _save_douyin_qrcode(page, account_file, qrcode_path, qrcode_callback=qrcode_callback)
            qrcode_path = Path(qrcode_info["image_path"])

        await asyncio.sleep(poll_interval)

    return _build_login_result(False, "timeout", "等待抖音扫码登录超时", account_file, qrcode_info, page.url)


async def douyin_cookie_gen(
    account_file,
    qrcode_callback=None,
    poll_interval: int = 3,
    max_checks: int = 100,
    headless: bool = LOCAL_CHROME_HEADLESS,
):
    browser, context = await open_douyin_cloak_context(headless=headless)
    qrcode_path = None
    result = _build_login_result(False, "failed", "抖音登录失败", account_file)
    try:
        page = await context.new_page()
        await page.goto("https://creator.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        qrcode_info = await _save_douyin_qrcode(page, account_file, qrcode_callback=qrcode_callback)
        qrcode_path = Path(qrcode_info["image_path"])
        douyin_logger.info(_msg("🧍", "请扫码，小人正在耐心等待登录完成"))
        result = await _wait_for_douyin_login(
            page,
            account_file,
            qrcode_info,
            qrcode_callback=qrcode_callback,
            poll_interval=poll_interval,
            max_checks=max_checks,
        )
        if result["success"]:
            await asyncio.sleep(2)
            await context.storage_state(path=account_file)
            if not await cookie_auth(account_file):
                result = _build_login_result(
                    False,
                    "cookie_invalid",
                    "抖音扫码流程结束，但 cookie 校验失败",
                    account_file,
                    qrcode_info,
                    page.url,
                )
    except Exception as exc:
        result = _build_login_result(False, "failed", str(exc), account_file, current_url=page.url if "page" in locals() else "")
    finally:
        if remove_qrcode_file(qrcode_path):
            douyin_logger.info(_msg("🧹", f"临时二维码文件已清理: {qrcode_path}"))
        if not result["success"]:
            douyin_logger.error(_msg("😢", f"登录失败: {result['message']}"))
        await context.close()
        await browser.close()
    return result


class DouYinBaseUploader(BaseVideoUploader):
    def __init__(
        self,
        publish_date: datetime | int,
        account_file,
        publish_strategy: str = DOUYIN_PUBLISH_STRATEGY_IMMEDIATE,
        debug: bool = DEBUG_MODE,
        headless: bool = LOCAL_CHROME_HEADLESS,
    ):
        self.publish_date = publish_date
        self.account_file = account_file
        self.publish_strategy = publish_strategy
        self.debug = debug
        self.date_format = "%Y年%m月%d日 %H:%M"
        self.local_executable_path = LOCAL_CHROME_PATH
        self.headless = headless

    async def validate_base_args(self):
        if not os.path.exists(self.account_file):
            raise RuntimeError(f"cookie文件不存在，请先完成抖音登录: {self.account_file}")
        if not await cookie_auth(self.account_file):
            raise RuntimeError(f"cookie文件已失效，请先完成抖音登录: {self.account_file}")
        if self.publish_strategy not in {DOUYIN_PUBLISH_STRATEGY_IMMEDIATE, DOUYIN_PUBLISH_STRATEGY_SCHEDULED}:
            raise ValueError(f"不支持的发布策略: {self.publish_strategy}")

        if self.publish_strategy == DOUYIN_PUBLISH_STRATEGY_SCHEDULED:
            self.publish_date = self.validate_publish_date(self.publish_date)
        else:
            self.publish_date = 0

    async def set_schedule_time_douyin(self, page, publish_date):
        label_element = page.locator("[class^='radio']:has-text('定时发布')")
        await label_element.click()
        await asyncio.sleep(1)
        publish_date_hour = publish_date.strftime("%Y-%m-%d %H:%M")

        await asyncio.sleep(1)
        await page.locator('.semi-input[placeholder="日期和时间"]').click()
        await page.keyboard.press("Control+KeyA")
        await page.keyboard.type(str(publish_date_hour))
        await page.keyboard.press("Enter")
        await asyncio.sleep(1)

    async def fill_title_and_description(self, page: Page, title: str, description: str, tags: list[str] | None = None):
        description_section = (
            page.get_by_text("作品描述", exact=True)
            .locator("xpath=ancestor::div[2]")
            .locator("xpath=following-sibling::div[1]")
        )

        title_input = description_section.locator('input[type="text"]').first
        await title_input.wait_for(state="visible", timeout=10000)
        await title_input.fill(title[:30])

        description_editor = description_section.locator('.zone-container[contenteditable="true"]').first
        await description_editor.wait_for(state="visible", timeout=10000)
        await description_editor.click()
        await page.keyboard.press("Control+KeyA")
        await page.keyboard.press("Delete")
        await page.keyboard.type(description)

        for tag in tags or []:
            await page.keyboard.type(" #" + tag)
            await page.keyboard.press("Space")

    async def set_location(self, page: Page, location: str = ""):
        if not location:
            return
        await page.locator('div.semi-select span:has-text("输入地理位置")').click()
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(2000)
        await page.keyboard.type(location)
        await page.wait_for_selector('div[role="listbox"] [role="option"]', timeout=5000)
        await page.locator('div[role="listbox"] [role="option"]').first.click()

    async def wait_for_video_publish_page(
        self,
        page: Page,
        timeout_seconds: int = DOUYIN_VIDEO_PUBLISH_PAGE_TIMEOUT_SECONDS,
    ) -> str:
        started_at = asyncio.get_running_loop().time()
        deadline = started_at + max(1, int(timeout_seconds))
        last_url = str(getattr(page, "url", "") or "")
        last_auth_state = "unknown"

        while True:
            current_url = str(getattr(page, "url", "") or "")
            if _is_douyin_video_publish_page_url(current_url):
                if "/post/video" in urlparse(current_url).path:
                    douyin_logger.info(_msg("🥳", "已经进入 version_2 发布页面"))
                    return "version_2"
                douyin_logger.info(_msg("🥳", "已经进入 version_1 发布页面"))
                return "version_1"

            last_url = current_url
            last_auth_state = await _douyin_auth_state(page)
            if last_auth_state == "login" or _is_douyin_login_url(current_url):
                screenshot = await _save_failure_screenshot(page, self.account_file, "douyin_login_redirect")
                raise RuntimeError(
                    f"抖音登录状态已失效或被重定向到登录页，当前 URL: {current_url}，现场截图: {screenshot}"
                )

            if asyncio.get_running_loop().time() >= deadline:
                screenshot = await _save_failure_screenshot(page, self.account_file, "douyin_publish_page_timeout")
                raise RuntimeError(
                    "等待抖音视频发布编辑页超时，"
                    f"已等待 {max(1, int(timeout_seconds))} 秒，"
                    f"当前 URL: {last_url or 'unknown'}，页面状态: {last_auth_state}，"
                    f"现场截图: {screenshot}"
                )

            douyin_logger.debug(_msg("🧍", "还没进到视频发布页面，小人继续等一会"))
            await asyncio.sleep(0.5)

    async def dismiss_upload_page_guides(self, page: Page, timeout_seconds: float = 2.0) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout_seconds)
        dismissed = False
        while True:
            for text in DOUYIN_UPLOAD_GUIDE_DISMISS_TEXTS:
                guide_button = page.locator(f'.shepherd-element button:has-text("{text}")').first
                if await _is_visible_locator(guide_button):
                    await guide_button.click()
                    douyin_logger.info(_msg("🥳", f"已关闭抖音上传页提示: {text}"))
                    dismissed = True
                    await asyncio.sleep(0.3)
                    continue

                button = page.get_by_role("button", name=text, exact=True).first
                if await _is_visible_locator(button):
                    await button.click()
                    douyin_logger.info(_msg("🥳", f"已关闭抖音上传页提示: {text}"))
                    dismissed = True
                    await asyncio.sleep(0.3)
                    continue

                text_locator = page.get_by_text(text, exact=True).first
                if await _is_visible_locator(text_locator):
                    await text_locator.click()
                    douyin_logger.info(_msg("🥳", f"已关闭抖音上传页提示: {text}"))
                    dismissed = True
                    await asyncio.sleep(0.3)
            if dismissed or asyncio.get_running_loop().time() >= deadline:
                return dismissed
            await asyncio.sleep(0.2)
        return dismissed

    async def upload_video_file_from_upload_page(self, page: Page, guide_timeout_seconds: float = 2.0) -> None:
        upload_input = page.locator(DOUYIN_VIDEO_UPLOAD_INPUT_SELECTOR).first
        await upload_input.wait_for(state="attached", timeout=15000)
        await self.dismiss_upload_page_guides(page, timeout_seconds=guide_timeout_seconds)

        upload_button = page.get_by_text(DOUYIN_VIDEO_UPLOAD_BUTTON_TEXT, exact=True).first
        if await _is_visible_locator(upload_button):
            try:
                async with page.expect_file_chooser(timeout=5000) as file_chooser_info:
                    await upload_button.click()
                file_chooser = await file_chooser_info.value
                await file_chooser.set_files(self.file_path)
                douyin_logger.info(_msg("📤", "已通过上传按钮选择视频文件"))
                await self.dismiss_upload_page_guides(page, timeout_seconds=guide_timeout_seconds)
                return
            except Exception as exc:
                douyin_logger.warning(_msg("😵", f"点击上传按钮没有打开文件选择器，改用文件输入框上传: {exc}"))

        try:
            await upload_input.set_input_files(self.file_path)
            douyin_logger.info(_msg("📤", "已通过视频文件输入框选择视频文件"))
            await self.dismiss_upload_page_guides(page, timeout_seconds=guide_timeout_seconds)
        except Exception as exc:
            screenshot = await _save_failure_screenshot(page, self.account_file, "douyin_video_upload_input_failed")
            raise RuntimeError(f"抖音视频文件上传入口不可用，现场截图: {screenshot}") from exc

    async def handle_declaration_modal(self, page: Page) -> bool:
        if await self._handle_direct_declaration_modal(page):
            return True
        return await self._handle_declaration_selection_modal(page)

    async def _handle_direct_declaration_modal(self, page: Page) -> bool:
        title = page.get_by_text(DOUYIN_DECLARATION_MODAL_TITLE, exact=True)
        button = page.get_by_role(
            "button",
            name=DOUYIN_DECLARATION_DIRECT_PUBLISH_BUTTON,
            exact=True,
        )
        if not await _is_visible_locator(title) or not await _is_visible_locator(button):
            return False

        await button.first.click()
        douyin_logger.info(_msg("🥳", "已确认“未添加自主声明”弹窗，继续发布"))
        return True

    async def _handle_declaration_selection_modal(self, page: Page) -> bool:
        modal = page.locator(
            f'{DOUYIN_MODAL_SELECTOR}:has-text("{DOUYIN_DECLARATION_SELECTION_MODAL_TITLE}")'
        ).first
        if not await _is_visible_locator(modal):
            return False

        option = modal.locator(f'label.semi-radio:has-text("{DOUYIN_DECLARATION_SELECTION_OPTION}")').first
        if not await _is_visible_locator(option):
            option = modal.get_by_text(DOUYIN_DECLARATION_SELECTION_OPTION, exact=True).first
        if not await _is_visible_locator(option):
            return False
        await option.first.click()
        confirm_button = modal.locator(
            f'button:has-text("{DOUYIN_DECLARATION_CONFIRM_BUTTON}"):not([disabled])'
        ).first
        for _ in range(30):
            if await _is_enabled_locator(confirm_button):
                await confirm_button.first.click()
                douyin_logger.info(_msg("🥳", "已选择“无需添加自主声明”，继续发布"))
                return True
            await asyncio.sleep(0.1)
        return False

    async def handle_product_dialog(self, page: Page, product_title: str):
        await page.wait_for_timeout(2000)
        await page.wait_for_selector('input[placeholder="请输入商品短标题"]', timeout=10000)
        short_title_input = page.locator('input[placeholder="请输入商品短标题"]')
        if not await short_title_input.count():
            douyin_logger.error(_msg("😵", "没找到商品短标题输入框"))
            return False

        product_title = product_title[:10]
        await short_title_input.fill(product_title)
        await page.wait_for_timeout(1000)

        finish_button = page.locator('button:has-text("完成编辑")')
        if "disabled" not in await finish_button.get_attribute("class"):
            await finish_button.click()
            douyin_logger.debug(_msg("🥳", "已点击“完成编辑”按钮"))
            await page.wait_for_selector(".semi-modal-content", state="hidden", timeout=5000)
            return True

        douyin_logger.error(_msg("😵", "“完成编辑”按钮是灰的，小人先把弹窗关掉"))
        cancel_button = page.locator('button:has-text("取消")')
        if await cancel_button.count():
            await cancel_button.click()
        else:
            close_button = page.locator(".semi-modal-close")
            await close_button.click()
        await page.wait_for_selector(".semi-modal-content", state="hidden", timeout=5000)
        return False

    async def set_product_link(self, page: Page, product_link: str, product_title: str):
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_selector("text=添加标签", timeout=10000)
            dropdown = page.get_by_text("添加标签").locator("..").locator("..").locator("..").locator(".semi-select").first
            if not await dropdown.count():
                douyin_logger.error(_msg("😵", "没找到标签下拉框"))
                return False
            douyin_logger.debug(_msg("🧍", "找到标签下拉框，小人准备选择“购物车”"))
            await dropdown.click()
            await page.wait_for_selector('[role="listbox"]', timeout=5000)
            await page.locator('[role="option"]:has-text("购物车")').click()
            douyin_logger.debug(_msg("🥳", "已经选中“购物车”"))

            await page.wait_for_selector('input[placeholder="粘贴商品链接"]', timeout=5000)
            input_field = page.locator('input[placeholder="粘贴商品链接"]')
            await input_field.fill(product_link)
            douyin_logger.debug(_msg("🔗", f"商品链接已经填好了: {product_link}"))

            add_button = page.locator('span:has-text("添加链接")')
            button_class = await add_button.get_attribute("class")
            if "disable" in button_class:
                douyin_logger.error(_msg("😵", "“添加链接”按钮现在点不了"))
                return False
            await add_button.click()
            douyin_logger.debug(_msg("🥳", "已点击“添加链接”按钮"))

            await page.wait_for_timeout(2000)
            error_modal = page.locator("text=未搜索到对应商品")
            if await error_modal.count():
                confirm_button = page.locator('button:has-text("确定")')
                await confirm_button.click()
                douyin_logger.error(_msg("😢", "这个商品链接无效"))
                return False

            if not await self.handle_product_dialog(page, product_title):
                return False

            douyin_logger.debug(_msg("🥳", "商品链接设置好了"))
            return True
        except Exception as e:
            douyin_logger.error(_msg("😢", f"设置商品链接时出错: {str(e)}"))
            return False


class DouYinVideo(DouYinBaseUploader):
    def __init__(
        self,
        title,
        file_path,
        tags,
        publish_date: datetime | int,
        account_file,
        thumbnail_landscape_path=None,
        productLink="",
        productTitle="",
        thumbnail_portrait_path=None,
        desc: str | None = None,
        publish_strategy: str = DOUYIN_PUBLISH_STRATEGY_IMMEDIATE,
        debug: bool = DEBUG_MODE,
        headless: bool = LOCAL_CHROME_HEADLESS,
    ):
        super().__init__(
            publish_date=publish_date,
            account_file=account_file,
            publish_strategy=publish_strategy,
            debug=debug,
            headless=headless,
        )
        self.title = title
        self.file_path = file_path
        self.tags = tags
        self.thumbnail_landscape_path = thumbnail_landscape_path
        self.thumbnail_portrait_path = thumbnail_portrait_path
        self.productLink = productLink
        self.productTitle = productTitle
        self.desc = desc or ""

    async def validate_upload_args(self):
        await self.validate_base_args()
        if not self.title or not str(self.title).strip():
            raise ValueError("视频模式下，title 是必须的")

        self.file_path = str(self.validate_video_file(self.file_path))
        if self.thumbnail_landscape_path:
            self.thumbnail_landscape_path = str(self.validate_image_file(self.thumbnail_landscape_path))
        if self.thumbnail_portrait_path:
            self.thumbnail_portrait_path = str(self.validate_image_file(self.thumbnail_portrait_path))

    async def handle_upload_error(self, page):
        douyin_logger.warning(_msg("😵", "视频上传摔了一跤，小人马上重新上传"))
        await page.locator('div.progress-div [class^="upload-btn-input"]').set_input_files(self.file_path)

    async def handle_auto_video_cover(self, page):
        if await page.get_by_text("请设置封面后再发布").first.is_visible():
            douyin_logger.info(_msg("🧍", "发布前还得先把封面弄好"))
            recommend_cover = page.locator('[class^="recommendCover-"]').first
            if await recommend_cover.count():
                douyin_logger.info(_msg("🏃", "小人去选第一个推荐封面"))
                try:
                    await recommend_cover.click()
                    await asyncio.sleep(1)
                    confirm_text = "是否确认应用此封面？"
                    if await page.get_by_text(confirm_text).first.is_visible():
                        douyin_logger.info(_msg("🪟", f"弹出确认框了: {confirm_text}"))
                        await page.get_by_role("button", name="确定").click()
                        douyin_logger.info(_msg("🥳", "推荐封面已经应用"))
                        await asyncio.sleep(1)
                    douyin_logger.info(_msg("🥳", "封面选择流程完成"))
                    return True
                except Exception as e:
                    douyin_logger.warning(_msg("😵", f"推荐封面没选成功: {e}"))
        return False

    async def set_thumbnail(self, page: Page):
        if not self.thumbnail_landscape_path and not self.thumbnail_portrait_path:
            return

        douyin_logger.info(_msg("🏃", "小人正在设置视频封面"))
        await page.click('text="选择封面"')
        cover_locator_str = 'div[id*="creator-content-modal"]'
        cover_locator = page.locator(cover_locator_str)
        await page.wait_for_selector(cover_locator_str)

        upload_input = cover_locator.locator("div[class^='semi-upload upload'] >> input.semi-upload-hidden-input")

        if self.thumbnail_landscape_path:
            await page.wait_for_timeout(1000)
            await upload_input.set_input_files(self.thumbnail_landscape_path)
            await page.wait_for_timeout(2000)
            douyin_logger.info(_msg("🖼️", "横版封面上传完成"))

        if self.thumbnail_portrait_path:
            await cover_locator.locator("div[class*='steps'] div").nth(1).click()
            await page.wait_for_timeout(1000)
            await upload_input.set_input_files(self.thumbnail_portrait_path)
            await page.wait_for_timeout(2000)
            douyin_logger.info(_msg("🖼️", "竖版封面上传完成"))

        await cover_locator.locator('button:visible:has-text("完成")').click()
        douyin_logger.info(_msg("🥳", "视频封面设置完成"))
        await page.wait_for_selector("div.extractFooter", state="detached")

    async def open_publish_context(self):
        return await open_douyin_cloak_context(
            headless=self.headless,
            context_options=build_douyin_context_options(self.account_file),
        )

    async def upload(self, _playwright=None) -> None:
        douyin_logger.info(_msg("🧍", "小人先检查 cookie、视频文件、封面和发布时间"))
        await self.validate_upload_args()
        douyin_logger.info(_msg("🥳", "上传前检查通过"))

        browser, context = await self.open_publish_context()
        try:
            page = await context.new_page()
            await page.goto(DOUYIN_UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            douyin_logger.info(_msg("🏃", f"小人开始搬运视频: {self.title}.mp4"))
            douyin_logger.info(_msg("🧭", "小人正在赶往上传主页"))
            await self.upload_video_file_from_upload_page(page)

            await self.wait_for_video_publish_page(page)

            await asyncio.sleep(1)
            douyin_logger.info(_msg("✍️", "小人开始填标题、描述和话题"))
            await self.fill_title_and_description(page, self.title, self.desc or self.title, self.tags)
            douyin_logger.info(_msg("🏷️", f"小人一共贴了 {len(self.tags)} 个话题"))

            while True:
                try:
                    number = await page.locator('[class^="long-card"] div:has-text("重新上传")').count()
                    if number > 0:
                        douyin_logger.success(_msg("🥳", "视频已经传完啦"))
                        break
                    douyin_logger.info(_msg("🏃", "小人正在努力上传视频"))
                    await asyncio.sleep(2)
                    if await page.locator('div.progress-div > div:has-text("上传失败")').count():
                        douyin_logger.error(_msg("😵", "检测到上传失败，小人准备重试"))
                        await self.handle_upload_error(page)
                except Exception:
                    douyin_logger.debug(_msg("🧍", "小人还在等视频上传完成"))
                    await asyncio.sleep(2)

            if self.productLink and self.productTitle:
                douyin_logger.info(_msg("🛒", "小人正在设置商品链接"))
                await self.set_product_link(page, self.productLink, self.productTitle)
                douyin_logger.info(_msg("🥳", "商品链接设置完成"))

            await self.set_thumbnail(page)

            third_part_element = '[class^="info"] > [class^="first-part"] div div.semi-switch'
            if await page.locator(third_part_element).count():
                if "semi-switch-checked" not in await page.eval_on_selector(third_part_element, "div => div.className"):
                    await page.locator(third_part_element).locator("input.semi-switch-native-control").click()

            if self.publish_strategy == DOUYIN_PUBLISH_STRATEGY_SCHEDULED and self.publish_date != 0:
                await self.set_schedule_time_douyin(page, self.publish_date)

            wait_after_declaration_confirm = False
            while True:
                try:
                    publish_button = page.get_by_role("button", name="发布", exact=True)
                    if not wait_after_declaration_confirm and await publish_button.count():
                        await publish_button.click()
                    await page.wait_for_url(
                        "https://creator.douyin.com/creator-micro/content/manage**",
                        timeout=3000,
                    )
                    douyin_logger.success(_msg("🥳", "视频发布成功，小人开心收工"))
                    break
                except Exception:
                    if await self.handle_declaration_modal(page):
                        wait_after_declaration_confirm = True
                    else:
                        wait_after_declaration_confirm = False
                        await self.handle_auto_video_cover(page)
                    douyin_logger.info(_msg("🏃", "小人正在冲刺发布视频"))
                    if self.debug:
                        await page.screenshot(full_page=True)
                    await asyncio.sleep(0.5)

            await context.storage_state(path=self.account_file)
            douyin_logger.success(_msg("🥳", "cookie 更新完毕"))
            await asyncio.sleep(2)
        finally:
            await context.close()
            await browser.close()

    async def douyin_upload_video(self):
        await self.upload()

    async def main(self):
        await self.douyin_upload_video()


class DouYinNote(DouYinBaseUploader):
    def __init__(
        self,
        image_paths,
        note,
        tags,
        publish_date: datetime | int,
        account_file,
        title: str | None = None,
        publish_strategy: str = DOUYIN_PUBLISH_STRATEGY_IMMEDIATE,
        debug: bool = DEBUG_MODE,
        headless: bool = LOCAL_CHROME_HEADLESS,
    ):
        super().__init__(
            publish_date=publish_date,
            account_file=account_file,
            publish_strategy=publish_strategy,
            debug=debug,
            headless=headless,
        )
        self.image_paths = image_paths
        self.note = note or ""
        self.title = title or (self.note[:30] if self.note else "")
        self.tags = tags or []

    async def validate_upload_args(self):
        await self.validate_base_args()
        if not self.title or not str(self.title).strip():
            raise ValueError("图文模式下，title 是必须的")
        if not self.image_paths:
            raise ValueError("图文模式下，图片是必须的")

        if isinstance(self.image_paths, (str, Path)):
            self.image_paths = [self.image_paths]

        if len(self.image_paths) > 35:
            raise ValueError("图文模式下最多只支持上传 35 张图片")

        normalized_image_paths = []
        for image_path in self.image_paths:
            normalized_image_paths.append(str(self.validate_image_file(image_path)))
        self.image_paths = normalized_image_paths

    async def upload_note_content(self, page: Page) -> None:
        douyin_logger.info(_msg("🏃", f"小人开始搬运图文，共 {len(self.image_paths)} 张图片"))
        douyin_logger.info(_msg("🔀", "小人正在切换到图文发布"))
        await page.get_by_text("发布图文", exact=True).click()
        await page.wait_for_timeout(1000)

        douyin_logger.info(_msg("📤", "小人正在上传图片"))
        await page.locator("div[class^='container'] input[accept*='image']").set_input_files(self.image_paths)

        while True:
            try:
                await page.wait_for_url(
                    "**/creator-micro/content/post/image?**",
                    timeout=3000,
                )
                douyin_logger.info(_msg("🥳", "已经进入图文发布页面"))
                break
            except Exception:
                douyin_logger.debug(_msg("🧍", "小人还在等图片上传完成"))
                await asyncio.sleep(0.5)

        await asyncio.sleep(1)
        douyin_logger.info(_msg("✍️", "小人开始填标题、描述和话题"))
        await self.fill_title_and_description(page, self.title, self.note, self.tags)
        douyin_logger.info(_msg("🏷️", f"小人一共贴了 {len(self.tags)} 个话题"))

        if self.publish_strategy == DOUYIN_PUBLISH_STRATEGY_SCHEDULED and self.publish_date != 0:
            await self.set_schedule_time_douyin(page, self.publish_date)

        wait_after_declaration_confirm = False
        while True:
            try:
                publish_button = page.get_by_role("button", name="发布", exact=True)
                if not wait_after_declaration_confirm and await publish_button.count():
                    await publish_button.click()
                await page.wait_for_url(
                    "**/creator-micro/content/manage?enter_from=publish**",
                    timeout=3000,
                )
                douyin_logger.success(_msg("🥳", "图文发布成功，小人开心收工"))
                break
            except Exception:
                wait_after_declaration_confirm = await self.handle_declaration_modal(page)
                douyin_logger.info(_msg("🏃", "小人正在冲刺发布图文"))
                await asyncio.sleep(0.5)

    async def open_publish_context(self):
        return await open_douyin_cloak_context(
            headless=self.headless,
            context_options=build_douyin_context_options(self.account_file),
        )

    async def upload(self, _playwright=None) -> None:
        douyin_logger.info(_msg("🧍", "小人先检查 cookie、图片和发布时间"))
        await self.validate_upload_args()
        douyin_logger.info(_msg("🥳", "图文上传前检查通过"))

        browser, context = await self.open_publish_context()

        upload_success = False
        try:
            page = await context.new_page()
            await page.goto(DOUYIN_UPLOAD_URL)
            douyin_logger.info(_msg("🧭", "小人正在赶往图文发布页"))
            await page.wait_for_url(DOUYIN_UPLOAD_URL)

            await self.upload_note_content(page)
            upload_success = True
        finally:
            if upload_success:
                await context.storage_state(path=self.account_file)
                douyin_logger.success(_msg("🥳", "cookie 更新完毕"))
                await asyncio.sleep(2)
            await context.close()
            await browser.close()

    async def douyin_upload_note(self):
        await self.upload()
