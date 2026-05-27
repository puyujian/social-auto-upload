import asyncio
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import sau_cli


class BilibiliCliTests(unittest.TestCase):
    def test_build_parser_accepts_bilibili_login(self):
        parser = sau_cli.build_parser()
        args = parser.parse_args(["bilibili", "login", "--account", "creator"])
        self.assertEqual(args.platform, "bilibili")
        self.assertEqual(args.action, "login")

    def test_build_parser_requires_tid_for_upload_video(self):
        parser = sau_cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "bilibili",
                    "upload-video",
                    "--account",
                    "creator",
                    "--file",
                    "demo.mp4",
                    "--title",
                    "hello",
                    "--desc",
                    "hello",
                ]
            )

    def test_build_parser_defaults_bilibili_upload_limit_to_one(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            video_path.write_bytes(b"video")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "bilibili",
                    "upload-video",
                    "--account",
                    "creator",
                    "--file",
                    str(video_path),
                    "--title",
                    "hello",
                    "--desc",
                    "hello",
                    "--tid",
                    "160",
                ]
            )

        self.assertEqual(args.limit, 1)
        self.assertEqual(args.line, "")
        self.assertEqual(args.submit, "")

    def test_upload_bilibili_video_passes_limit_and_line_to_biliup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cookie = root / "bilibili_creator.json"
            cookie.write_text("{}", encoding="utf-8")
            video = root / "demo.mp4"
            video.write_bytes(b"video")
            calls = []

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            def fake_account_file(_platform, _account):
                return cookie

            def fake_run(arguments):
                calls.append(arguments)
                return Result()

            request = sau_cli.BilibiliVideoUploadRequest(
                account_name="creator",
                video_file=video,
                title="hello",
                description="desc",
                tid=160,
                tags=["tag1", "tag2"],
                publish_date=0,
                upload_limit=1,
                upload_line="tx",
                submit="web",
            )

            with patch("sau_cli.resolve_account_file", side_effect=fake_account_file), patch(
                "sau_cli.run_biliup_command",
                side_effect=fake_run,
            ):
                asyncio.run(sau_cli.upload_bilibili_video(request))

        self.assertIn("--limit", calls[0])
        self.assertEqual(calls[0][calls[0].index("--limit") + 1], "1")
        self.assertIn("--line", calls[0])
        self.assertEqual(calls[0][calls[0].index("--line") + 1], "tx")
        self.assertIn("--submit", calls[0])
        self.assertEqual(calls[0][calls[0].index("--submit") + 1], "web")
        self.assertIn("--tag", calls[0])
        self.assertEqual(calls[0][calls[0].index("--tag") + 1], "tag1,tag2")

    def test_dispatch_bilibili_check_prints_valid(self):
        args = Namespace(platform="bilibili", action="check", account="creator")
        with patch("sau_cli.check_bilibili_account", new=AsyncMock(return_value=True)):
            code = asyncio.run(sau_cli.dispatch(args))
        self.assertEqual(code, 0)

    def test_login_bilibili_account_returns_friendly_message_without_terminal(self):
        with patch("sau_cli.has_interactive_terminal", return_value=False):
            result = asyncio.run(sau_cli.login_bilibili_account("creator"))
        self.assertFalse(result["success"])
        self.assertIn("local interactive terminal", result["message"].lower())
        self.assertIn("qrcode.png", result["message"].lower())
