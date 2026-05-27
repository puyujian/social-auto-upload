# -*- coding: utf-8 -*-
"""HumanBehavior 模块单元测试。"""
import asyncio
import math
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import utils.human_behavior as hb


# ---------------------------------------------------------------------------
# 配置测试
# ---------------------------------------------------------------------------

class HumanBehaviorConfigTests(unittest.TestCase):
    def test_default_aggression_is_medium(self):
        config = hb.HumanBehaviorConfig()
        self.assertEqual(config.aggression_level, "medium")

    def test_apply_low_reduces_delay_range(self):
        config = hb.HumanBehaviorConfig(aggression_level="low")
        config.apply_aggression()
        self.assertLess(config.delay_range[1], 2.0)
        self.assertLess(config.mouse_move_steps, 20)
        self.assertLess(config.mouse_jitter_px, 2.0)

    def test_apply_high_increases_delay_range(self):
        config = hb.HumanBehaviorConfig(aggression_level="high")
        config.apply_aggression()
        self.assertGreaterEqual(config.delay_range[1], 3.5)
        self.assertGreaterEqual(config.mouse_move_steps, 35)
        self.assertGreaterEqual(config.mouse_jitter_px, 3.0)

    def test_create_config_sets_aggression_and_overrides(self):
        config = hb.create_config("low", delay_range=(0.1, 0.5))
        self.assertEqual(config.aggression_level, "low")
        self.assertEqual(config.delay_range, (0.1, 0.5))

    def test_create_config_from_conf_maps_human_behavior_settings(self):
        class FakeConf:
            HUMAN_BEHAVIOR_AGGRESSION = "high"
            HUMAN_BEHAVIOR_DELAY_RANGE = [0.2, 0.9]
            HUMAN_BEHAVIOR_TYPING_SPEED_RANGE = (10, 20)
            HUMAN_BEHAVIOR_CLICK_OFFSET = (-1.0, 1.0)
            HUMAN_BEHAVIOR_RANDOMIZE_VIEWPORT = False
            HUMAN_BEHAVIOR_ROTATE_UA = False
            HUMAN_BEHAVIOR_RANDOMIZE_FINGERPRINT = False

        config = hb._create_config_from_conf(FakeConf)

        self.assertEqual(config.aggression_level, "high")
        self.assertEqual(config.delay_range, (0.2, 0.9))
        self.assertEqual(config.typing_speed_range, (10, 20))
        self.assertEqual(config.click_offset_range, (-1.0, 1.0))
        self.assertFalse(config.randomize_viewport)
        self.assertFalse(config.rotate_user_agent)
        self.assertFalse(config.randomize_timezone)
        self.assertFalse(config.randomize_language)
        self.assertFalse(config.randomize_canvas_fingerprint)
        self.assertFalse(config.randomize_webgl_fingerprint)


# ---------------------------------------------------------------------------
# HumanBehaviorModule 测试
# ---------------------------------------------------------------------------

class HumanBehaviorModuleTests(unittest.TestCase):
    def setUp(self):
        self.human = hb.HumanBehaviorModule()

    def test_default_aggression_is_medium(self):
        self.assertEqual(self.human.config.aggression_level, "medium")

    def test_set_aggression_level_returns_self(self):
        result = self.human.set_aggression_level("high")
        self.assertIs(result, self.human)
        self.assertEqual(self.human.config.aggression_level, "high")

    def test_set_delay_range(self):
        self.human.set_delay_range((0.1, 0.5))
        self.assertEqual(self.human.config.delay_range, (0.1, 0.5))

    def test_set_typing_speed(self):
        self.human.set_typing_speed(30, 100)
        self.assertEqual(self.human.config.typing_speed_range, (30, 100))

    def test_set_click_offset(self):
        self.human.set_click_offset(-3.0, 3.0)
        self.assertEqual(self.human.config.click_offset_range, (-3.0, 3.0))

    def test_with_config_factory(self):
        m = hb.HumanBehaviorModule.with_config("high", delay_range=(1.0, 5.0))
        self.assertEqual(m.config.aggression_level, "high")
        self.assertEqual(m.config.delay_range, (1.0, 5.0))

    def test_shuffle_accounts_does_not_modify_original(self):
        original = [1, 2, 3, 4, 5]
        shuffled = self.human.shuffle_accounts(original)
        self.assertEqual(sorted(shuffled), sorted(original))
        self.assertEqual(original, [1, 2, 3, 4, 5])


# ---------------------------------------------------------------------------
# 贝塞尔曲线测试
# ---------------------------------------------------------------------------

class BezierTests(unittest.TestCase):
    def test_bezier_start_point(self):
        p = hb._bezier_point((0, 0), (10, 0), (90, 0), (100, 0), 0.0)
        self.assertAlmostEqual(p[0], 0.0)
        self.assertAlmostEqual(p[1], 0.0)

    def test_bezier_end_point(self):
        p = hb._bezier_point((0, 0), (10, 0), (90, 0), (100, 0), 1.0)
        self.assertAlmostEqual(p[0], 100.0)
        self.assertAlmostEqual(p[1], 0.0)

    def test_bezier_midpoint(self):
        p = hb._bezier_point((0, 0), (25, 25), (75, 75), (100, 100), 0.5)
        self.assertAlmostEqual(p[0], 50.0)
        self.assertAlmostEqual(p[1], 50.0)

    def test_ease_in_out_start_slow(self):
        t_values = [hb._ease_in_out_cubic(t) for t in (0.0, 0.1, 0.2)]
        diffs = [t_values[i + 1] - t_values[i] for i in range(len(t_values) - 1)]
        mid = [hb._ease_in_out_cubic(t) for t in (0.4, 0.5, 0.6)]
        mid_diffs = [mid[i + 1] - mid[i] for i in range(len(mid) - 1)]
        # 开头增速应小于中间
        self.assertLess(diffs[0], mid_diffs[0])

    def test_ease_in_out_end_slow(self):
        late = [hb._ease_in_out_cubic(t) for t in (0.8, 0.9, 1.0)]
        mid = [hb._ease_in_out_cubic(t) for t in (0.4, 0.5, 0.6)]
        late_avg = (late[1] - late[0] + (late[2] - late[1])) / 2
        mid_avg = (mid[1] - mid[0] + (mid[2] - mid[1])) / 2
        self.assertLess(late_avg, mid_avg)


# ---------------------------------------------------------------------------
# 正态分布随机延迟测试
# ---------------------------------------------------------------------------

class RandomDelayTests(unittest.TestCase):
    def test_clamped_gauss_respects_bounds(self):
        for _ in range(200):
            val = hb._clamped_gauss(mean=1.0, std=0.3, lo=0.5, hi=2.0)
            self.assertGreaterEqual(val, 0.5)
            self.assertLessEqual(val, 2.0)

    def test_human_sleep_with_explicit_seconds(self):
        async def _test():
            start = asyncio.get_running_loop().time()
            await hb.human_sleep(0.05)
            elapsed = asyncio.get_running_loop().time() - start
            self.assertGreaterEqual(elapsed, 0.04)

        asyncio.run(_test())

    def test_human_sleep_random_delay(self):
        config = hb.HumanBehaviorConfig(aggression_level="low")
        config.apply_aggression()
        config.delay_range = (0.01, 0.05)

        async def _test():
            await hb.human_sleep(config=config)

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# 点击坐标随机化测试
# ---------------------------------------------------------------------------

class ClickRandomizationTests(unittest.TestCase):
    def _mock_page(self, box=None):
        page = MagicMock()
        page.viewport_size = {"width": 1920, "height": 1080}
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock()
        page.mouse.click = AsyncMock()

        locator = AsyncMock()
        locator.wait_for = AsyncMock(return_value=None)
        locator.bounding_box = AsyncMock(
            return_value=box or {"x": 100, "y": 200, "width": 80, "height": 30}
        )
        page.locator = MagicMock(return_value=locator)
        page.locator.return_value.first = locator
        return page

    def test_human_click_within_element_bounds(self):
        page = self._mock_page()
        config = hb.create_config("low")

        async def _test():
            await hb.human_click(page, "#btn", config=config)
            call_args = page.mouse.click.call_args
            click_x = call_args[0][0]
            click_y = call_args[0][1]
            self.assertGreater(click_x, 97)
            self.assertLess(click_x, 183)
            self.assertGreater(click_y, 197)
            self.assertLess(click_y, 233)

        asyncio.run(_test())

    def test_human_click_with_locator(self):
        page = self._mock_page()
        locator = page.locator("#btn").first
        config = hb.create_config("low")

        async def _test():
            await hb.human_click(page, locator=locator, config=config)
            self.assertTrue(page.mouse.click.called)

        asyncio.run(_test())

    def test_human_click_keeps_tiny_element_click_inside_bounds(self):
        page = self._mock_page(box={"x": 10, "y": 20, "width": 4, "height": 4})
        config = hb.create_config("high")

        async def _test():
            await hb.human_click(page, "#tiny", config=config)
            click_x, click_y = page.mouse.click.call_args[0][:2]
            self.assertGreaterEqual(click_x, 10)
            self.assertLessEqual(click_x, 14)
            self.assertGreaterEqual(click_y, 20)
            self.assertLessEqual(click_y, 24)

        asyncio.run(_test())

    def test_human_click_raises_without_selector_or_locator(self):
        page = self._mock_page()

        async def _test():
            with self.assertRaises(ValueError):
                await hb.human_click(page)

        asyncio.run(_test())

    def test_human_click_mouse_moves_before_click(self):
        page = self._mock_page()
        config = hb.create_config("low")

        async def _test():
            await hb.human_click(page, "#btn", config=config)
            self.assertTrue(page.mouse.move.called)
            self.assertTrue(page.mouse.click.called)

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# 打字节奏测试
# ---------------------------------------------------------------------------

class TypingRhythmTests(unittest.TestCase):
    def test_human_type_calls_keyboard_type(self):
        page = MagicMock()
        page.keyboard = AsyncMock()
        page.keyboard.type = AsyncMock()
        page.keyboard.press = AsyncMock()
        config = hb.create_config("low")
        config.typing_mistake_probability = 0  # no mistakes for deterministic test

        async def _test():
            await hb.human_type(page, "hello", config=config)
            self.assertTrue(page.keyboard.type.called)

        asyncio.run(_test())

    def test_human_type_short_text(self):
        page = MagicMock()
        page.keyboard = AsyncMock()
        page.keyboard.type = AsyncMock()
        page.keyboard.press = AsyncMock()
        config = hb.create_config("low")
        config.typing_mistake_probability = 0

        async def _test():
            await hb.human_type(page, "ab", config=config)
            self.assertEqual(page.keyboard.type.call_count, 2)

        asyncio.run(_test())

    def test_human_type_think_on_space(self):
        page = MagicMock()
        page.keyboard = AsyncMock()
        page.keyboard.type = AsyncMock()
        page.keyboard.press = AsyncMock()
        config = hb.create_config("low")
        config.typing_mistake_probability = 0

        async def _test():
            await hb.human_type(page, "a b", config=config)
            self.assertEqual(page.keyboard.type.call_count, 3)  # 'a', ' ', 'b'

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# 滚动行为测试
# ---------------------------------------------------------------------------

class ScrollBehaviorTests(unittest.TestCase):
    def test_human_random_scroll_calls_wheel(self):
        page = MagicMock()
        page.mouse = AsyncMock()
        page.mouse.wheel = AsyncMock()
        config = hb.create_config("low")
        config.scroll_segments_min = 2
        config.scroll_segments_max = 4

        async def _test():
            await hb.human_random_scroll(page, config=config, min_distance=100, max_distance=300)
            self.assertTrue(page.mouse.wheel.called)

        asyncio.run(_test())

    def test_human_scroll_into_view_gets_bounding_box(self):
        page = MagicMock()
        page.viewport_size = {"width": 1920, "height": 1080}
        page.mouse = AsyncMock()
        page.mouse.wheel = AsyncMock()
        page.evaluate = AsyncMock(return_value=0)
        page.locator = MagicMock()
        locator = AsyncMock()
        locator.wait_for = AsyncMock(return_value=None)
        locator.bounding_box = AsyncMock(
            return_value={"x": 100, "y": 1200, "width": 80, "height": 30}
        )
        page.locator.return_value.first = locator
        config = hb.create_config("low")
        config.scroll_segments_min = 2
        config.scroll_segments_max = 4

        async def _test():
            await hb.human_scroll_into_view(page, "#target", config=config)
            self.assertTrue(page.mouse.wheel.called)

        asyncio.run(_test())

    def test_human_scroll_into_view_uses_viewport_relative_bounding_box(self):
        page = MagicMock()
        page.viewport_size = {"width": 1920, "height": 800}
        page.mouse = AsyncMock()
        page.mouse.wheel = AsyncMock()
        page.evaluate = AsyncMock(return_value=1000)
        page.locator = MagicMock()
        locator = AsyncMock()
        locator.wait_for = AsyncMock(return_value=None)
        locator.bounding_box = AsyncMock(
            return_value={"x": 100, "y": 1000, "width": 80, "height": 40}
        )
        page.locator.return_value.first = locator
        config = hb.create_config("low")
        config.scroll_segments_min = 1
        config.scroll_segments_max = 1

        async def _test():
            await hb.human_scroll_into_view(page, "#target", config=config)
            scroll_delta = page.mouse.wheel.call_args[0][1]
            self.assertGreater(scroll_delta, 0)
            page.evaluate.assert_not_called()

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# 浏览器指纹混淆测试
# ---------------------------------------------------------------------------

class FingerprintObfuscationTests(unittest.TestCase):
    def test_random_canvas_noise_is_non_empty(self):
        noise = hb._random_canvas_noise()
        self.assertGreater(len(noise), 0)
        self.assertTrue(all(c in "0123456789abcdef" for c in noise))

    def test_random_webgl_noise_is_non_empty(self):
        noise = hb._random_webgl_noise()
        self.assertGreater(len(noise), 0)

    def test_inject_fingerprint_noise_injects_scripts(self):
        page = MagicMock()
        page.add_init_script = AsyncMock()
        config = hb.create_config("medium")

        async def _test():
            await hb._inject_fingerprint_noise(page, config)
            self.assertGreater(page.add_init_script.call_count, 0)

        asyncio.run(_test())

    def test_inject_fingerprint_noise_no_op_when_all_disabled(self):
        page = MagicMock()
        page.add_init_script = AsyncMock()
        config = hb.HumanBehaviorConfig()
        config.randomize_canvas_fingerprint = False
        config.randomize_webgl_fingerprint = False
        config.randomize_timezone = False
        config.randomize_language = False

        async def _test():
            await hb._inject_fingerprint_noise(page, config)
            self.assertEqual(page.add_init_script.call_count, 0)

        asyncio.run(_test())

    def test_apply_fingerprint_obfuscation_sets_context_user_agent_header(self):
        page = MagicMock()
        page.set_viewport_size = AsyncMock()
        page.add_init_script = AsyncMock()
        context = MagicMock()
        context.set_extra_http_headers = AsyncMock()
        config = hb.HumanBehaviorConfig()
        config.randomize_viewport = False
        config.randomize_canvas_fingerprint = False
        config.randomize_webgl_fingerprint = False
        config.randomize_timezone = False
        config.randomize_language = False
        config.rotate_user_agent = True

        async def _test():
            await hb.apply_fingerprint_obfuscation(page, context, config=config)
            headers = context.set_extra_http_headers.call_args[0][0]
            self.assertIn("User-Agent", headers)
            script = page.add_init_script.call_args[0][0]
            self.assertIn(headers["User-Agent"], script)

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# 行为模式测试
# ---------------------------------------------------------------------------

class BehaviorPatternTests(unittest.TestCase):
    def test_randomize_account_order_returns_shuffled_copy(self):
        accounts = ["acct_a", "acct_b", "acct_c", "acct_d", "acct_e"]
        result = hb.randomize_account_order(accounts)
        self.assertEqual(sorted(result), sorted(accounts))
        self.assertEqual(accounts, ["acct_a", "acct_b", "acct_c", "acct_d", "acct_e"])

    def test_randomize_account_order_handles_empty(self):
        self.assertEqual(hb.randomize_account_order([]), [])

    def test_randomize_account_order_handles_single(self):
        self.assertEqual(hb.randomize_account_order(["only"]), ["only"])


# ---------------------------------------------------------------------------
# 集成测试：HumanBehaviorModule 完整流程
# ---------------------------------------------------------------------------

class IntegrationTests(unittest.TestCase):
    def test_full_module_chain(self):
        human = hb.HumanBehaviorModule()
        human.set_aggression_level("low")
        human.set_delay_range((0.1, 0.3))
        human.set_typing_speed(30, 80)
        human.set_click_offset(-2.0, 2.0)

        self.assertEqual(human.config.aggression_level, "low")
        self.assertEqual(human.config.delay_range, (0.1, 0.3))
        self.assertEqual(human.config.typing_speed_range, (30, 80))
        self.assertEqual(human.config.click_offset_range, (-2.0, 2.0))

    def test_module_click_integration(self):
        human = hb.HumanBehaviorModule.with_config("low")
        page = MagicMock()
        page.viewport_size = {"width": 1920, "height": 1080}
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock()
        page.mouse.click = AsyncMock()

        locator = AsyncMock()
        locator.wait_for = AsyncMock(return_value=None)
        locator.bounding_box = AsyncMock(
            return_value={"x": 50, "y": 50, "width": 100, "height": 40}
        )
        page.locator = MagicMock(return_value=locator)
        page.locator.return_value.first = locator

        async def _test():
            await human.click(page, "#test-btn")

        asyncio.run(_test())

    def test_module_click_accepts_explicit_position(self):
        human = hb.HumanBehaviorModule.with_config("low")
        human.config.click_offset_range = (0, 0)
        page = MagicMock()
        page.viewport_size = {"width": 1920, "height": 1080}
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock()
        page.mouse.click = AsyncMock()

        locator = AsyncMock()
        locator.wait_for = AsyncMock(return_value=None)
        locator.bounding_box = AsyncMock(
            return_value={"x": 50, "y": 60, "width": 100, "height": 40}
        )

        async def _test():
            await human.click(page, locator=locator, position={"x": 12, "y": 18})

        asyncio.run(_test())

        page.mouse.click.assert_awaited_once()
        args = page.mouse.click.await_args.args
        self.assertEqual(args[:2], (62, 78))

    def test_module_type_integration(self):
        human = hb.HumanBehaviorModule.with_config("low")
        human.config.typing_mistake_probability = 0
        page = MagicMock()
        page.keyboard = AsyncMock()
        page.keyboard.type = AsyncMock()
        page.keyboard.press = AsyncMock()

        async def _test():
            await human.type(page, "test")

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
