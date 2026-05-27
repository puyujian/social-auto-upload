# -*- coding: utf-8 -*-
"""HumanBehavior module — 模拟真人操作行为，降低自动化被识别为机器人的风险。

核心能力:
  - 贝塞尔曲线鼠标轨迹模拟（加减速 + 微抖动）
  - 点击坐标随机化（目标元素范围内随机偏移）
  - 正态分布随机延迟
  - 真人打字节奏模拟
  - 分段滚动 + 惯性模拟
  - 浏览器指纹混淆（UA / 视口 / 时区 / 语言 / Canvas / WebGL）
  - 发布前随机浏览、帐号顺序随机化等行为模式
"""

import asyncio
import math
import random
import time as _time
from dataclasses import dataclass
from typing import Any, Optional, Tuple, List


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class HumanBehaviorConfig:
    """人类行为模块的配置参数。

    aggression_level 控制激进程度:
      - "low":    最小级别的反检测，操作间隔贴近自动化但仍有少量随机偏移
      - "medium": 中等反检测，明显的随机化但不会过于拖慢流程
      - "high":   高度反检测，所有参数都最大化随机化，流程会明显变慢
    """

    aggression_level: str = "medium"  # low / medium / high

    # ---- delay 相关 ----
    delay_range: Tuple[float, float] = (0.3, 2.5)  # 操作间随机延迟 (秒)
    delay_mean: float = 1.2  # 正态分布均值
    delay_std: float = 0.6  # 正态分布标准差
    page_load_delay_range: Tuple[float, float] = (1.0, 4.0)  # 页面加载后等待

    # ---- 鼠标移动 ----
    mouse_move_steps: int = 25  # 贝塞尔曲线采样点数
    mouse_jitter_px: float = 2.0  # 轨迹抖动幅度 (像素)
    mouse_ease_in_out: bool = True  # 是否启用缓入缓出

    # ---- 点击 ----
    click_offset_range: Tuple[float, float] = (-5.0, 5.0)  # 点击坐标随机偏移

    # ---- 打字 ----
    typing_speed_range: Tuple[int, int] = (50, 150)  # 每字符延迟 (ms)
    typing_think_range: Tuple[float, float] = (0.3, 1.5)  # 思考停顿 (秒)
    typing_mistake_probability: float = 0.02  # 故意打错再删除的概率

    # ---- 滚动 ----
    scroll_segments_min: int = 4  # 分段滚动最少段数
    scroll_segments_max: int = 10  # 分段滚动最多段数
    scroll_momentum_factor: float = 0.85  # 惯性衰减因子

    # ---- 指纹混淆 ----
    randomize_viewport: bool = True
    randomize_timezone: bool = True
    randomize_language: bool = True
    randomize_canvas_fingerprint: bool = True
    randomize_webgl_fingerprint: bool = True
    rotate_user_agent: bool = True

    def apply_aggression(self):
        """根据 aggression_level 调整所有参数。"""
        if self.aggression_level == "low":
            self.delay_range = (0.2, 1.5)
            self.delay_mean = 0.6
            self.delay_std = 0.3
            self.page_load_delay_range = (0.5, 2.0)
            self.mouse_move_steps = 15
            self.mouse_jitter_px = 1.0
            self.click_offset_range = (-2.0, 2.0)
            self.typing_speed_range = (40, 100)
            self.typing_think_range = (0.2, 0.8)
            self.typing_mistake_probability = 0.005
            self.scroll_segments_min = 3
            self.scroll_segments_max = 6
        elif self.aggression_level == "high":
            self.delay_range = (0.5, 4.0)
            self.delay_mean = 2.0
            self.delay_std = 1.0
            self.page_load_delay_range = (2.0, 6.0)
            self.mouse_move_steps = 40
            self.mouse_jitter_px = 4.0
            self.click_offset_range = (-8.0, 8.0)
            self.typing_speed_range = (60, 200)
            self.typing_think_range = (0.5, 3.0)
            self.typing_mistake_probability = 0.05
            self.scroll_segments_min = 6
            self.scroll_segments_max = 15


# ---------------------------------------------------------------------------
# UUID 生成 (用于 canvas/webgl 指纹)
# ---------------------------------------------------------------------------

_NOISE_POOL = "0123456789abcdef"


def _random_hex(length: int) -> str:
    return "".join(random.choice(_NOISE_POOL) for _ in range(length))


def _random_canvas_noise() -> str:
    return _random_hex(random.randint(8, 32))


def _random_webgl_noise() -> str:
    return _random_hex(random.randint(12, 64))


def _random_webgl_param() -> str:
    return _random_hex(random.randint(8, 32))


# ---------------------------------------------------------------------------
# 正态分布随机延迟
# ---------------------------------------------------------------------------

def _clamped_gauss(mean: float, std: float, lo: float, hi: float) -> float:
    val = random.gauss(mean, std)
    return max(lo, min(hi, val))


async def human_sleep(
    seconds: Optional[float] = None,
    *,
    config: Optional[HumanBehaviorConfig] = None,
) -> None:
    """使用正态分布的随机延迟，比固定 sleep 更自然。"""
    if seconds is not None:
        await asyncio.sleep(seconds)
        return

    cfg = config or _default_config
    delay = _clamped_gauss(cfg.delay_mean, cfg.delay_std, cfg.delay_range[0], cfg.delay_range[1])
    await asyncio.sleep(delay)


def create_config(aggression_level: str = "medium", **overrides) -> HumanBehaviorConfig:
    """快捷创建配置。

    Usage:
        config = create_config("high", delay_range=(0.5, 3.0))
    """
    config = HumanBehaviorConfig(aggression_level=aggression_level)
    config.apply_aggression()
    for key, value in overrides.items():
        if hasattr(config, key):
            setattr(config, key, value)
    return config


def _pair(value: Any) -> Any:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (value[0], value[1])
    return value


def _create_config_from_conf(conf_module: Any) -> HumanBehaviorConfig:
    aggression = getattr(conf_module, "HUMAN_BEHAVIOR_AGGRESSION", "medium")
    overrides = {}

    attr_map = {
        "HUMAN_BEHAVIOR_DELAY_RANGE": "delay_range",
        "HUMAN_BEHAVIOR_TYPING_SPEED_RANGE": "typing_speed_range",
        "HUMAN_BEHAVIOR_CLICK_OFFSET": "click_offset_range",
        "HUMAN_BEHAVIOR_RANDOMIZE_VIEWPORT": "randomize_viewport",
        "HUMAN_BEHAVIOR_ROTATE_UA": "rotate_user_agent",
    }
    for conf_attr, config_attr in attr_map.items():
        if hasattr(conf_module, conf_attr):
            overrides[config_attr] = _pair(getattr(conf_module, conf_attr))

    if hasattr(conf_module, "HUMAN_BEHAVIOR_RANDOMIZE_FINGERPRINT"):
        enabled = getattr(conf_module, "HUMAN_BEHAVIOR_RANDOMIZE_FINGERPRINT")
        overrides.update(
            {
                "randomize_timezone": enabled,
                "randomize_language": enabled,
                "randomize_canvas_fingerprint": enabled,
                "randomize_webgl_fingerprint": enabled,
            }
        )

    return create_config(aggression, **overrides)


def _load_default_config() -> HumanBehaviorConfig:
    try:
        import conf  # type: ignore
    except ModuleNotFoundError as exc:
        if exc.name == "conf":
            return create_config()
        raise
    return _create_config_from_conf(conf)


# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------

_default_config = _load_default_config()


def get_default_config() -> HumanBehaviorConfig:
    return _default_config


def set_default_config(config: HumanBehaviorConfig):
    global _default_config
    _default_config = config
    _default_config.apply_aggression()


# ---------------------------------------------------------------------------
# 贝塞尔曲线鼠标移动
# ---------------------------------------------------------------------------

def _bezier_point(p0, p1, p2, p3, t):
    """三次贝塞尔曲线 (cubic bezier)。"""
    mt = 1 - t
    mt2 = mt * mt
    mt3 = mt2 * mt
    t2 = t * t
    t3 = t2 * t
    x = mt3 * p0[0] + 3 * mt2 * t * p1[0] + 3 * mt * t2 * p2[0] + t3 * p3[0]
    y = mt3 * p0[1] + 3 * mt2 * t * p1[1] + 3 * mt * t2 * p2[1] + t3 * p3[1]
    return (x, y)


def _ease_in_out_cubic(t: float) -> float:
    """缓入缓出函数。"""
    if t < 0.5:
        return 4 * t * t * t
    else:
        return 1 - pow(-2 * t + 2, 3) / 2


async def human_mouse_move(
    page,
    from_pos: Tuple[float, float],
    to_pos: Tuple[float, float],
    *,
    config: Optional[HumanBehaviorConfig] = None,
) -> None:
    """使用贝塞尔曲线模拟鼠标从 from_pos 移动到 to_pos。

    Args:
        page: Playwright Page 对象
        from_pos: 起始坐标 (x, y)
        to_pos: 目标坐标 (x, y)
        config: 行为配置
    """
    cfg = config or _default_config

    # 随机控制点
    dx = to_pos[0] - from_pos[0]
    dy = to_pos[1] - from_pos[1]
    dist = math.hypot(dx, dy)

    if dist < 2:
        await page.mouse.move(to_pos[0], to_pos[1])
        return

    cp1 = (
        from_pos[0] + dx * random.uniform(0.2, 0.45) + random.uniform(-dist * 0.1, dist * 0.1),
        from_pos[1] + dy * random.uniform(0.1, 0.3) + random.uniform(-dist * 0.05, dist * 0.05),
    )
    cp2 = (
        from_pos[0] + dx * random.uniform(0.55, 0.8) + random.uniform(-dist * 0.1, dist * 0.1),
        from_pos[1] + dy * random.uniform(0.7, 0.9) + random.uniform(-dist * 0.05, dist * 0.05),
    )

    steps = cfg.mouse_move_steps

    for i in range(steps + 1):
        raw_t = i / steps
        t = _ease_in_out_cubic(raw_t) if cfg.mouse_ease_in_out else raw_t

        x, y = _bezier_point(from_pos, cp1, cp2, to_pos, t)

        # 微抖动
        if cfg.mouse_jitter_px > 0 and 0.1 < t < 0.9:
            x += random.uniform(-cfg.mouse_jitter_px, cfg.mouse_jitter_px)
            y += random.uniform(-cfg.mouse_jitter_px, cfg.mouse_jitter_px)

        await page.mouse.move(x, y)
        # 每一步之间也有微小延迟
        await asyncio.sleep(random.uniform(0.003, 0.012))


# ---------------------------------------------------------------------------
# 点击坐标随机化
# ---------------------------------------------------------------------------

async def human_click(
    page,
    selector: Optional[str] = None,
    *,
    locator=None,
    config: Optional[HumanBehaviorConfig] = None,
    button: str = "left",
    click_count: int = 1,
    position: Optional[dict] = None,
    timeout: Optional[float] = None,
) -> None:
    """在目标元素范围内随机选择位置点击。

    支持两种方式指定目标:
      - selector:       CSS/XPath 选择器
      - locator:        Playwright Locator 对象

    Args:
        page: Playwright Page 对象
        selector: CSS/XPath 选择器
        locator: Playwright Locator 对象（优先于 selector）
        config: 行为配置
        button: 鼠标按键 (left/right/middle)
        click_count: 点击次数 (1=单击, 2=双击)
        position: 指定确切位置 {x, y}
    """
    cfg = config or _default_config

    if locator is not None:
        target = locator
    elif selector is not None:
        target = page.locator(selector).first
    else:
        raise ValueError("必须提供 selector 或 locator 参数")

    # 等待元素可见
    await target.wait_for(state="visible", timeout=timeout or 10000)

    # 获取元素包围盒
    box = await target.bounding_box()
    if box is None:
        raise RuntimeError(f"无法获取元素的包围盒: {selector or locator}")

    if position is not None:
        click_x = box["x"] + position.get("x", 0)
        click_y = box["y"] + position.get("y", 0)
    else:
        # 在元素中心附近随机偏移
        offset_range = cfg.click_offset_range
        offset_x = random.uniform(*offset_range)
        offset_y = random.uniform(*offset_range)
        # 确保不超出元素边界
        margin = 3
        max_ox = max(0, box["width"] / 2 - margin)
        max_oy = max(0, box["height"] / 2 - margin)
        offset_x = max(-max_ox, min(max_ox, offset_x))
        offset_y = max(-max_oy, min(max_oy, offset_y))

        click_x = box["x"] + box["width"] / 2 + offset_x
        click_y = box["y"] + box["height"] / 2 + offset_y

    # 获取当前鼠标位置（估计）
    viewport = page.viewport_size
    current_mouse_x = viewport["width"] / 2 if viewport else 500
    current_mouse_y = viewport["height"] / 2 if viewport else 400

    # 先移动鼠标
    await human_mouse_move(page, (current_mouse_x, current_mouse_y), (click_x, click_y), config=cfg)

    # 点击前微小停顿
    await asyncio.sleep(random.uniform(0.03, 0.12))

    # 点击
    await page.mouse.click(click_x, click_y, button=button, click_count=click_count)


async def human_scroll_into_view(
    page,
    locator_or_selector,
    *,
    config: Optional[HumanBehaviorConfig] = None,
) -> None:
    """分段随机滚动到目标元素，模拟手指滑动惯性。

    Args:
        page: Playwright Page 对象
        locator_or_selector: Playwright Locator 或 CSS 选择器
        config: 行为配置
    """
    cfg = config or _default_config

    if isinstance(locator_or_selector, str):
        target = page.locator(locator_or_selector).first
    else:
        target = locator_or_selector

    await target.wait_for(state="visible", timeout=10000)

    box = await target.bounding_box()
    if box is None:
        raise RuntimeError("无法获取目标元素包围盒")

    viewport = page.viewport_size or {"width": 1920, "height": 1080}
    element_y = box["y"] + box["height"] / 2

    # 分段滚动
    segments = random.randint(cfg.scroll_segments_min, cfg.scroll_segments_max)

    total_distance = element_y - viewport["height"] / 2

    if abs(total_distance) < 10:
        return

    remaining = total_distance
    for i in range(segments):
        progress = i / segments
        # 开始快，后面慢 — 模拟惯性
        segment_ratio = 1 / (segments - i)
        momentum = cfg.scroll_momentum_factor ** (i + 1)
        step = remaining * segment_ratio * momentum

        if i == segments - 1:
            step = remaining  # 最后一步精确到位

        await page.mouse.wheel(0, step)
        remaining -= step

        # 每段之间微小延迟
        await asyncio.sleep(random.uniform(0.03, 0.15))


# ---------------------------------------------------------------------------
# 打字节奏模拟
# ---------------------------------------------------------------------------

async def human_type(
    page,
    text: str,
    *,
    config: Optional[HumanBehaviorConfig] = None,
    field_locator=None,
) -> None:
    """模拟真人打字：每字符随机间隔、随机思考停顿、偶发打错重来。

    Args:
        page: Playwright Page 对象
        text: 要输入的文本
        config: 行为配置
        field_locator: 可选，先点击该元素再打字
    """
    cfg = config or _default_config

    if field_locator:
        await human_click(page, locator=field_locator, config=cfg)
        await asyncio.sleep(random.uniform(0.1, 0.3))

    chars = list(text)
    i = 0

    while i < len(chars):
        ch = chars[i]

        # 模拟思考停顿（在空格、换行、标点前）
        if ch in (" ", "\n", ".", "，", "。", "！", "？", ",", "!", "?"):
            think_gap = random.uniform(*cfg.typing_think_range)
            await asyncio.sleep(think_gap)

        # 偶发打错
        if random.random() < cfg.typing_mistake_probability and ch.isalpha():
            # 打一个相邻键位的字符
            wrong_char = _nearby_key(ch)
            if wrong_char:
                await page.keyboard.type(wrong_char)
                char_delay_ms = random.randint(*cfg.typing_speed_range)
                await asyncio.sleep(char_delay_ms / 1000)
                await page.keyboard.press("Backspace")
                await asyncio.sleep(char_delay_ms / 1000 * 1.5)

        await page.keyboard.type(ch)
        char_delay_ms = random.randint(*cfg.typing_speed_range)
        await asyncio.sleep(char_delay_ms / 1000)
        i += 1


def _nearby_key(ch: str) -> Optional[str]:
    """返回键盘上相邻键位的字符，用于模拟打错。"""
    keyboard_map = {
        "a": "s", "b": "v", "c": "x", "d": "f", "e": "r",
        "f": "g", "g": "h", "h": "j", "i": "o", "j": "k",
        "k": "l", "l": "k", "m": "n", "n": "m", "o": "i",
        "p": "o", "q": "w", "r": "t", "s": "a", "t": "y",
        "u": "y", "v": "c", "w": "e", "x": "z", "y": "u",
        "z": "x",
    }
    return keyboard_map.get(ch.lower())


# ---------------------------------------------------------------------------
# 滚动行为
# ---------------------------------------------------------------------------

async def human_random_scroll(
    page,
    *,
    config: Optional[HumanBehaviorConfig] = None,
    direction: str = "down",
    min_distance: int = 100,
    max_distance: int = 2000,
    pause_probability: float = 0.3,
) -> None:
    """模拟随意的页面浏览滚动。

    Args:
        page: Playwright Page 对象
        config: 行为配置
        direction: 滚动方向 (up/down)
        min_distance: 最小滚动距离 (px)
        max_distance: 最大滚动距离 (px)
        pause_probability: 中途暂停概率
    """
    cfg = config or _default_config

    total = random.randint(min_distance, max_distance)
    segments = random.randint(cfg.scroll_segments_min, cfg.scroll_segments_max)
    remaining = total
    sign = -1 if direction == "up" else 1

    for i in range(segments):
        segment_ratio = 1 / (segments - i)
        momentum = cfg.scroll_momentum_factor ** (i + 1)
        step = sign * remaining * segment_ratio * momentum

        if i == segments - 1:
            step = sign * remaining

        scroll_delta = int(step)
        await page.mouse.wheel(0, scroll_delta)
        remaining -= abs(scroll_delta)

        await asyncio.sleep(random.uniform(0.05, 0.25))

        # 随机在中途暂停看内容
        if random.random() < pause_probability:
            await asyncio.sleep(random.uniform(0.5, 2.5))


# ---------------------------------------------------------------------------
# 页面导航
# ---------------------------------------------------------------------------

async def human_goto(
    page,
    url: str,
    *,
    config: Optional[HumanBehaviorConfig] = None,
    wait_until: str = "domcontentloaded",
) -> None:
    """导航到 URL，加载后等待随机时间。

    Args:
        page: Playwright Page 对象
        url: 目标 URL
        config: 行为配置
        wait_until: Playwright 加载状态
    """
    cfg = config or _default_config

    await page.goto(url, wait_until=wait_until)

    # 页面加载后随机等待
    delay = random.uniform(*cfg.page_load_delay_range)
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# 浏览器指纹混淆
# ---------------------------------------------------------------------------

# 常用真实 UA 轮换池
_USER_AGENT_POOL = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    # Chrome on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
]

# 常见视口
_VIEWPORT_POOL = [
    {"width": 1920, "height": 1080},
    {"width": 2560, "height": 1440},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1680, "height": 1050},
]

# 常见时区
_TIMEZONE_POOL = [
    "Asia/Shanghai",
    "Asia/Hong_Kong",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Asia/Taipei",
]

# 常见语言
_LANGUAGE_POOL = [
    "zh-CN,zh;q=0.9,en;q=0.8",
    "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "zh,en;q=0.9,ja;q=0.8",
    "zh-CN;q=0.9,zh-TW;q=0.8,en;q=0.7",
]

# 常见 platform
_PLATFORM_POOL = [
    "Win32",
    "Win32",
    "Win32",
    "Linux i686",
    "MacIntel",
]

# 常见硬件并发数
_HARDWARE_CONCURRENCY_POOL = [4, 8, 12, 16, 24]

# 常见设备内存 (GB)
_DEVICE_MEMORY_POOL = [4, 8, 16]


async def _randomize_viewport(page, config: HumanBehaviorConfig) -> None:
    if not config.randomize_viewport:
        return
    vp = random.choice(_VIEWPORT_POOL)
    await page.set_viewport_size(vp)


async def _inject_fingerprint_noise(page, config: HumanBehaviorConfig) -> None:
    """注入 JS 代码随机化浏览器指纹。"""
    parts = []

    if config.randomize_canvas_fingerprint:
        parts.append(f"""
        // Canvas fingerprint noise
        const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(...args) {{
            const ctx = this.getContext('2d');
            if (ctx) {{
                const noise = "{_random_canvas_noise()}";
                ctx.fillStyle = 'rgba(255,255,255,0.001)';
                ctx.fillText(noise, 0, 1);
            }}
            return _origToDataURL.apply(this, args);
        }};
        const _origToBlob = HTMLCanvasElement.prototype.toBlob;
        HTMLCanvasElement.prototype.toBlob = function(callback, ...args) {{
            const ctx = this.getContext('2d');
            if (ctx) {{
                const noise = "{_random_canvas_noise()}";
                ctx.fillStyle = 'rgba(255,255,255,0.001)';
                ctx.fillText(noise, 0, 1);
            }}
            return _origToBlob.call(this, callback, ...args);
        }};
        """)

    if config.randomize_webgl_fingerprint:
        parts.append(f"""
        // WebGL fingerprint noise
        const _origGetParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {{
            if (param === 37445) return "{_random_webgl_noise()}";  // UNMASKED_VENDOR_WEBGL
            if (param === 37446) return "{_random_webgl_noise()}";  // UNMASKED_RENDERER_WEBGL
            if (param === 3415) return 0;  // MAX_VERTEX_ATTRIBS (randomize slightly)
            return _origGetParameter.call(this, param);
        }};
        const _origGetParameter2 = WebGL2RenderingContext && WebGL2RenderingContext.prototype.getParameter;
        if (_origGetParameter2) {{
            WebGL2RenderingContext.prototype.getParameter = function(param) {{
                if (param === 37445) return "{_random_webgl_noise()}";
                if (param === 37446) return "{_random_webgl_noise()}";
                return _origGetParameter2.call(this, param);
            }};
        }}
        """)

    if config.randomize_timezone:
        zone = random.choice(_TIMEZONE_POOL)
        parts.append(f"""
        // Timezone
        Object.defineProperty(Intl.DateTimeFormat.prototype, 'resolvedOptions', {{
            value: function() {{
                const orig = {{
                    locale: 'zh-CN',
                    calendar: 'gregory',
                    numberingSystem: 'latn',
                    timeZone: '{zone}',
                }};
                return orig;
            }}
        }});
        """)

    if config.randomize_language:
        lang = random.choice(_LANGUAGE_POOL)
        platform = random.choice(_PLATFORM_POOL)
        hw = random.choice(_HARDWARE_CONCURRENCY_POOL)
        mem = random.choice(_DEVICE_MEMORY_POOL)
        parts.append(f"""
        // Navigator overrides
        Object.defineProperty(navigator, 'languages', {{ get: () => [{', '.join(repr(l.strip()) for l in lang.split(','))}] }});
        Object.defineProperty(navigator, 'language', {{ get: () => '{lang.split(',')[0].split(';')[0].strip()}' }});
        Object.defineProperty(navigator, 'platform', {{ get: () => '{platform}' }});
        Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw} }});
        Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {mem} }});
        Object.defineProperty(navigator, 'maxTouchPoints', {{ get: () => {random.randint(0, 5)} }});
        """)

    if parts:
        script = "(function() {\n" + "\n".join(parts) + "\n})()"
        await page.add_init_script(script)


async def apply_fingerprint_obfuscation(
    page,
    context,
    *,
    config: Optional[HumanBehaviorConfig] = None,
) -> None:
    """为页面应用浏览器指纹混淆。

    应在页面创建后、导航前调用。覆盖:
      - User-Agent 随机化
      - 视口随机化
      - Canvas 指纹加噪
      - WebGL 指纹加噪
      - 时区 / 语言 / 平台 / 硬件信息随机化

    Args:
        page: Playwright Page 对象
        context: Playwright BrowserContext 对象
        config: 行为配置
    """
    cfg = config or _default_config

    # 视口
    await _randomize_viewport(page, cfg)

    # 注入 JS 指纹混淆
    await _inject_fingerprint_noise(page, cfg)

    # 随机 UA - 在 context 级别设置
    # 注意: Playwright 不支持在页面上直接修改 navigator.userAgent
    # 我们通过 init_script 覆盖
    if cfg.rotate_user_agent:
        ua = random.choice(_USER_AGENT_POOL)
        set_headers = getattr(context, "set_extra_http_headers", None)
        if set_headers is not None:
            await set_headers({"User-Agent": ua})
        await page.add_init_script(f"""
        Object.defineProperty(navigator, 'userAgent', {{
            get: () => '{ua}',
            configurable: true
        }});
        Object.defineProperty(navigator, 'userAgentData', {{
            get: () => undefined,
            configurable: true
        }});
        """)


# ---------------------------------------------------------------------------
# 行为模式
# ---------------------------------------------------------------------------

async def human_browse_randomly(
    page,
    *,
    config: Optional[HumanBehaviorConfig] = None,
    duration_seconds: float = 5.0,
) -> None:
    """在页面上随机滚动"浏览内容"，模拟真人看一眼。

    Args:
        page: Playwright Page 对象
        config: 行为配置
        duration_seconds: 浏览总时长
    """
    cfg = config or _default_config
    start = _time.time()

    while _time.time() - start < duration_seconds:
        direction = random.choice(["down", "down", "up"])
        await human_random_scroll(
            page,
            config=cfg,
            direction=direction,
            min_distance=200,
            max_distance=800,
            pause_probability=0.4,
        )
        # 滚动之间随机停顿"看内容"
        await asyncio.sleep(random.uniform(0.8, 3.0))


def randomize_account_order(accounts: List) -> List:
    """随机化帐号操作顺序，避免每次都从同一个帐号开始。

    Args:
        accounts: 帐号列表

    Returns:
        随机排序后的帐号列表（不修改原列表）
    """
    shuffled = list(accounts)
    random.shuffle(shuffled)
    return shuffled


async def human_idle_before_action(
    *,
    config: Optional[HumanBehaviorConfig] = None,
) -> None:
    """在重要操作前模拟一个"思考/浏览"的随机空闲间隔。

    Args:
        config: 行为配置
    """
    cfg = config or _default_config
    delay = random.uniform(*cfg.page_load_delay_range)
    await asyncio.sleep(delay)


async def human_pre_browse(
    page,
    urls: Optional[List[str]] = None,
    *,
    config: Optional[HumanBehaviorConfig] = None,
) -> None:
    """在发布操作前随机浏览一些内容，模拟真实用户的完整行为。

    会随机跳转到提供的 URL 列表中的页面（如果提供），
    在每个页面停留并滚动以模拟阅读。

    Args:
        page: Playwright Page 对象
        urls: 可选的 URL 列表，用于随机浏览
        config: 行为配置
    """
    if urls:
        browse_url = random.choice(urls)
        await human_goto(page, browse_url, config=config)
        await human_browse_randomly(page, config=config, duration_seconds=random.uniform(2.0, 6.0))


# ---------------------------------------------------------------------------
# HumanBehavior 模块（高级封装）
# ---------------------------------------------------------------------------

class HumanBehaviorModule:
    """人类行为模块 — 可配置的反检测操作封装。

    Usage:
        human = HumanBehaviorModule()
        human.set_aggression_level("high")
        human.set_delay_range((0.5, 3.0))

        await human.click(page, "#button")
        await human.type(page, "Hello world")
        await human.scroll(page, element)
    """

    def __init__(self, aggression_level: str = "medium"):
        self.config = HumanBehaviorConfig(aggression_level=aggression_level)
        self.config.apply_aggression()
        self._last_mouse_pos: Optional[Tuple[float, float]] = None

    # ---- 配置 ----

    def set_aggression_level(self, level: str) -> "HumanBehaviorModule":
        """设置反检测激进程度: 'low' / 'medium' / 'high'。"""
        self.config.aggression_level = level
        self.config.apply_aggression()
        return self

    def set_delay_range(self, delay_range: Tuple[float, float]) -> "HumanBehaviorModule":
        """设置操作间随机延迟范围 (秒)。"""
        self.config.delay_range = delay_range
        return self

    def set_delay_distribution(self, mean: float, std: float) -> "HumanBehaviorModule":
        """设置延迟的正态分布参数。"""
        self.config.delay_mean = mean
        self.config.delay_std = std
        return self

    def set_typing_speed(self, min_ms: int, max_ms: int) -> "HumanBehaviorModule":
        """设置打字速度范围 (每字符毫秒数)。"""
        self.config.typing_speed_range = (min_ms, max_ms)
        return self

    def set_click_offset(self, min_px: float, max_px: float) -> "HumanBehaviorModule":
        """设置点击坐标随机偏移范围。"""
        self.config.click_offset_range = (min_px, max_px)
        return self

    def set_page_load_delay(self, lo: float, hi: float) -> "HumanBehaviorModule":
        """设置页面加载后等待范围。"""
        self.config.page_load_delay_range = (lo, hi)
        return self

    # ---- 核心操作 ----

    async def delay(
        self,
        seconds: Optional[float] = None,
    ) -> "HumanBehaviorModule":
        """添加一个随机（或固定）的延迟。"""
        await human_sleep(seconds, config=self.config)
        return self

    async def move_mouse(
        self,
        page,
        to_pos: Tuple[float, float],
        from_pos: Optional[Tuple[float, float]] = None,
    ) -> "HumanBehaviorModule":
        """模拟鼠标移动到指定位置。"""
        if from_pos is None:
            from_pos = self._last_mouse_pos or self._estimate_mouse_pos(page)
        await human_mouse_move(page, from_pos, to_pos, config=self.config)
        self._last_mouse_pos = to_pos
        return self

    async def click(
        self,
        page,
        selector: Optional[str] = None,
        *,
        locator=None,
        button: str = "left",
        click_count: int = 1,
        position: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> "HumanBehaviorModule":
        """模拟人类点击行为。"""
        await human_click(
            page,
            selector=selector,
            locator=locator,
            config=self.config,
            button=button,
            click_count=click_count,
            position=position,
            timeout=timeout,
        )
        return self

    async def type(
        self,
        page,
        text: str,
        *,
        field_locator=None,
    ) -> "HumanBehaviorModule":
        """模拟人类打字节奏。"""
        await human_type(page, text, config=self.config, field_locator=field_locator)
        return self

    async def scroll(
        self,
        page,
        locator_or_selector,
    ) -> "HumanBehaviorModule":
        """分段滚动到目标元素。"""
        await human_scroll_into_view(page, locator_or_selector, config=self.config)
        return self

    async def goto(
        self,
        page,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
    ) -> "HumanBehaviorModule":
        """导航到 URL 并等待随机时长。"""
        await human_goto(page, url, config=self.config, wait_until=wait_until)
        return self

    async def random_scroll(
        self,
        page,
        *,
        direction: str = "down",
        min_distance: int = 100,
        max_distance: int = 2000,
    ) -> "HumanBehaviorModule":
        """模拟随意浏览滚动。"""
        await human_random_scroll(
            page,
            config=self.config,
            direction=direction,
            min_distance=min_distance,
            max_distance=max_distance,
        )
        return self

    async def pre_browse(
        self,
        page,
        urls: Optional[List[str]] = None,
    ) -> "HumanBehaviorModule":
        """发布前随机浏览。"""
        await human_pre_browse(page, urls=urls, config=self.config)
        return self

    async def apply_fingerprint_obfuscation(
        self,
        page,
        context,
    ) -> "HumanBehaviorModule":
        """应用浏览器指纹混淆。"""
        await apply_fingerprint_obfuscation(page, context, config=self.config)
        return self

    # ---- 工具 ----

    def _estimate_mouse_pos(self, page) -> Tuple[float, float]:
        """估算当前鼠标位置（视口中心）。"""
        try:
            vp = page.viewport_size
            if vp:
                return (vp["width"] / 2, vp["height"] / 2)
        except Exception:
            pass
        return (960, 540)

    @classmethod
    def with_config(cls, aggression_level: str = "medium", **overrides) -> "HumanBehaviorModule":
        """快捷创建带配置的模块实例。"""
        m = cls(aggression_level)
        for key, value in overrides.items():
            if hasattr(m.config, key):
                setattr(m.config, key, value)
        return m

    def shuffle_accounts(self, accounts: List) -> List:
        """随机化帐号操作顺序。"""
        return randomize_account_order(accounts)
