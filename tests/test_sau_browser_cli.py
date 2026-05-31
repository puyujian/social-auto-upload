import asyncio
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import sau_cli


class BrowserCliParserTests(unittest.TestCase):
    def test_build_parser_accepts_xiaohongshu_login(self):
        parser = sau_cli.build_parser()
        args = parser.parse_args(["xiaohongshu", "login", "--account", "creator"])
        self.assertEqual(args.platform, "xiaohongshu")
        self.assertEqual(args.action, "login")

    def test_douyin_upload_video_accepts_desc(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            video_path.write_bytes(b"video")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "douyin",
                    "upload-video",
                    "--account",
                    "creator",
                    "--file",
                    str(video_path),
                    "--title",
                    "标题",
                    "--desc",
                    "视频简介",
                ]
            )

        self.assertEqual(args.desc, "视频简介")

    def test_kuaishou_upload_note_accepts_title_and_note(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "1.png"
            image_path.write_bytes(b"image")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "kuaishou",
                    "upload-note",
                    "--account",
                    "creator",
                    "--images",
                    str(image_path),
                    "--title",
                    "图文标题",
                    "--note",
                    "图文正文",
                ]
            )

        self.assertEqual(args.title, "图文标题")
        self.assertEqual(args.note, "图文正文")

    def test_xiaohongshu_upload_video_defaults_to_headed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            video_path.write_bytes(b"video")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "xiaohongshu",
                    "upload-video",
                    "--account",
                    "creator",
                    "--file",
                    str(video_path),
                    "--title",
                    "视频标题",
                    "--group-chat",
                    "手作交流群",
                ]
            )

        self.assertFalse(args.headless)
        self.assertEqual(args.group_chat, "手作交流群")
        self.assertEqual(args.publish_mode, "browser")
        self.assertFalse(args.confirm_protocol_publish)

    def test_xiaohongshu_upload_video_accepts_protocol_publish_mode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "demo.mp4"
            video_path.write_bytes(b"video")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "xiaohongshu",
                    "upload-video",
                    "--account",
                    "creator",
                    "--file",
                    str(video_path),
                    "--title",
                    "视频标题",
                    "--publish-mode",
                    "protocol",
                    "--confirm-protocol-publish",
                ]
            )

        self.assertEqual(args.publish_mode, "protocol")
        self.assertTrue(args.confirm_protocol_publish)

    def test_xiaohongshu_upload_note_accepts_headless(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "1.png"
            image_path.write_bytes(b"image")

            parser = sau_cli.build_parser()
            args = parser.parse_args(
                [
                    "xiaohongshu",
                    "upload-note",
                    "--account",
                    "creator",
                    "--images",
                    str(image_path),
                    "--title",
                    "图文标题",
                    "--note",
                    "图文正文",
                    "--headless",
                ]
            )

        self.assertTrue(args.headless)


class BrowserCliDispatchTests(unittest.TestCase):
    def test_dispatch_xiaohongshu_check_prints_valid(self):
        args = Namespace(platform="xiaohongshu", action="check", account="creator")
        with patch("sau_cli.check_xiaohongshu_account", new=AsyncMock(return_value=True)):
            code = asyncio.run(sau_cli.dispatch(args))
        self.assertEqual(code, 0)

    def test_dispatch_douyin_upload_note_uses_new_request_fields(self):
        args = Namespace(
            platform="douyin",
            action="upload-note",
            account="creator",
            images=[Path("1.png")],
            title="图文标题",
            note="图文正文",
            tags="测试,图文",
            schedule=0,
            debug=False,
            headless=True,
        )
        with patch("sau_cli.upload_note", new=AsyncMock()) as mock_upload:
            asyncio.run(sau_cli.dispatch(args))

        request = mock_upload.await_args.args[0]
        self.assertEqual(request.title, "图文标题")
        self.assertEqual(request.note, "图文正文")

    def test_dispatch_xiaohongshu_upload_video_uses_headed_request(self):
        args = Namespace(
            platform="xiaohongshu",
            action="upload-video",
            account="creator",
            file=Path("demo.mp4"),
            title="视频标题",
            desc="视频简介",
            tags="测试,视频",
            schedule=0,
            thumbnail=None,
            group_chat="手作交流群",
            publish_mode="browser",
            confirm_protocol_publish=False,
            debug=False,
            headless=False,
        )
        with patch("sau_cli.upload_xiaohongshu_video", new=AsyncMock()) as mock_upload:
            asyncio.run(sau_cli.dispatch(args))

        request = mock_upload.await_args.args[0]
        self.assertEqual(request.title, "视频标题")
        self.assertEqual(request.description, "视频简介")
        self.assertEqual(request.group_chat, "手作交流群")
        self.assertEqual(request.publish_mode, "browser")
        self.assertFalse(request.headless)

    def test_dispatch_xiaohongshu_upload_note_uses_default_headed_request(self):
        args = Namespace(
            platform="xiaohongshu",
            action="upload-note",
            account="creator",
            images=[Path("1.png"), Path("2.png")],
            title="图文标题",
            note="图文正文",
            tags="测试,图文",
            schedule=0,
            group_chat="图文群",
            publish_mode="browser",
            confirm_protocol_publish=False,
            debug=False,
            headless=False,
        )
        with patch("sau_cli.upload_xiaohongshu_note", new=AsyncMock()) as mock_upload:
            asyncio.run(sau_cli.dispatch(args))

        request = mock_upload.await_args.args[0]
        self.assertEqual(request.title, "图文标题")
        self.assertEqual(request.note, "图文正文")
        self.assertEqual(request.group_chat, "图文群")
        self.assertEqual(request.publish_mode, "browser")
        self.assertFalse(request.headless)
        self.assertEqual(len(request.image_files), 2)

    def test_dispatch_xiaohongshu_protocol_request_includes_mode(self):
        args = Namespace(
            platform="xiaohongshu",
            action="upload-video",
            account="creator",
            file=Path("demo.mp4"),
            title="视频标题",
            desc="视频简介",
            tags="测试,视频",
            schedule=0,
            thumbnail=None,
            group_chat="",
            publish_mode="protocol",
            confirm_protocol_publish=True,
            debug=False,
            headless=False,
        )
        with patch("sau_cli.upload_xiaohongshu_video", new=AsyncMock()) as mock_upload:
            asyncio.run(sau_cli.dispatch(args))

        request = mock_upload.await_args.args[0]
        self.assertEqual(request.publish_mode, "protocol")
        self.assertTrue(request.confirm_protocol_publish)

    def test_xiaohongshu_protocol_video_allows_group_chat(self):
        class FakeProtocol:
            calls = []

            def __init__(self, account_file):
                self.account_file = account_file

            def publish_video(self, **kwargs):
                FakeProtocol.calls.append(kwargs)
                return {"success": True}

        request = sau_cli.XiaohongshuVideoUploadRequest(
            account_name="creator",
            video_file=Path("demo.mp4"),
            title="视频标题",
            description="视频简介",
            tags=["测试"],
            publish_date=0,
            group_chat="手作交流群",
            publish_mode="protocol",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "xiaohongshu_creator.json"
            account_file.write_text("{}", encoding="utf-8")
            with patch("sau_cli.resolve_account_file", return_value=account_file):
                with patch("sau_cli.XhsCreatorProtocol", FakeProtocol):
                    asyncio.run(sau_cli.upload_xiaohongshu_video(request))

        self.assertEqual(FakeProtocol.calls[0]["group_chat"], "手作交流群")

    def test_xiaohongshu_protocol_video_rejects_unverified_thumbnail(self):
        request = sau_cli.XiaohongshuVideoUploadRequest(
            account_name="creator",
            video_file=Path("demo.mp4"),
            title="视频标题",
            description="视频简介",
            tags=["测试"],
            publish_date=0,
            thumbnail_file=Path("cover.png"),
            publish_mode="protocol",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "xiaohongshu_creator.json"
            account_file.write_text("{}", encoding="utf-8")
            with patch("sau_cli.resolve_account_file", return_value=account_file):
                with self.assertRaisesRegex(RuntimeError, "封面"):
                    asyncio.run(sau_cli.upload_xiaohongshu_video(request))

    def test_xiaohongshu_protocol_video_rejects_schedule(self):
        request = sau_cli.XiaohongshuVideoUploadRequest(
            account_name="creator",
            video_file=Path("demo.mp4"),
            title="视频标题",
            description="视频简介",
            tags=["测试"],
            publish_date=sau_cli.datetime(2026, 6, 1, 12, 0),
            publish_strategy=sau_cli.XIAOHONGSHU_PUBLISH_STRATEGY_SCHEDULED,
            publish_mode="protocol",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "xiaohongshu_creator.json"
            account_file.write_text("{}", encoding="utf-8")
            with patch("sau_cli.resolve_account_file", return_value=account_file):
                with self.assertRaisesRegex(RuntimeError, "定时"):
                    asyncio.run(sau_cli.upload_xiaohongshu_video(request))

    def test_xiaohongshu_protocol_video_calls_protocol_module(self):
        class FakeProtocol:
            kwargs = None
            calls = []

            def __init__(self, account_file):
                FakeProtocol.kwargs = {"account_file": account_file}

            def publish_video(self, **kwargs):
                FakeProtocol.calls.append(kwargs)
                return {"success": True}

        request = sau_cli.XiaohongshuVideoUploadRequest(
            account_name="creator",
            video_file=Path("demo.mp4"),
            title="视频标题",
            description="视频简介",
            tags=["测试"],
            publish_date=0,
            publish_mode="protocol",
            confirm_protocol_publish=True,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "xiaohongshu_creator.json"
            account_file.write_text("{}", encoding="utf-8")
            with patch("sau_cli.resolve_account_file", return_value=account_file):
                with patch("sau_cli.XhsCreatorProtocol", FakeProtocol):
                    asyncio.run(sau_cli.upload_xiaohongshu_video(request))

        self.assertEqual(FakeProtocol.kwargs["account_file"], account_file)
        self.assertEqual(FakeProtocol.calls[0]["title"], "视频标题")
        self.assertEqual(FakeProtocol.calls[0]["group_chat"], "")
        self.assertTrue(FakeProtocol.calls[0]["confirm_publish"])

    def test_xiaohongshu_video_upload_does_not_open_cookie_probe_before_publish(self):
        class FakeVideoUploader:
            kwargs = None

            def __init__(self, **kwargs):
                FakeVideoUploader.kwargs = kwargs

            async def main(self):
                return None

        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "xiaohongshu_creator.json"
            account_file.write_text("{}")
            request = sau_cli.XiaohongshuVideoUploadRequest(
                account_name="creator",
                video_file=Path("demo.mp4"),
                title="视频标题",
                description="视频简介",
                tags=["测试"],
                publish_date=0,
            )

            with patch("sau_cli.resolve_account_file", return_value=account_file):
                with patch("sau_cli.xiaohongshu_setup", new=AsyncMock()) as mock_setup:
                    with patch("sau_cli.XiaoHongShuVideo", FakeVideoUploader):
                        asyncio.run(sau_cli.upload_xiaohongshu_video(request))

        mock_setup.assert_not_awaited()
        self.assertTrue(FakeVideoUploader.kwargs["cookie_verified"])

    def test_xiaohongshu_note_upload_does_not_open_cookie_probe_before_publish(self):
        class FakeNoteUploader:
            kwargs = None

            def __init__(self, **kwargs):
                FakeNoteUploader.kwargs = kwargs

            async def main(self):
                return None

        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "xiaohongshu_creator.json"
            account_file.write_text("{}")
            request = sau_cli.XiaohongshuNoteUploadRequest(
                account_name="creator",
                image_files=[Path("demo.png")],
                title="图文标题",
                note="图文正文",
                tags=["测试"],
                publish_date=0,
            )

            with patch("sau_cli.resolve_account_file", return_value=account_file):
                with patch("sau_cli.xiaohongshu_setup", new=AsyncMock()) as mock_setup:
                    with patch("sau_cli.XiaoHongShuNote", FakeNoteUploader):
                        asyncio.run(sau_cli.upload_xiaohongshu_note(request))

        mock_setup.assert_not_awaited()
        self.assertTrue(FakeNoteUploader.kwargs["cookie_verified"])

    def test_kuaishou_video_upload_does_not_open_cookie_probe_before_publish(self):
        class FakeVideoUploader:
            kwargs = None

            def __init__(self, **kwargs):
                FakeVideoUploader.kwargs = kwargs

            async def main(self):
                return None

        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "kuaishou_creator.json"
            account_file.write_text("{}")
            request = sau_cli.KuaishouVideoUploadRequest(
                account_name="creator",
                video_file=Path("demo.mp4"),
                title="视频标题",
                description="视频简介",
                tags=["测试"],
                publish_date=0,
            )

            with patch("sau_cli.resolve_account_file", return_value=account_file):
                with patch("sau_cli.ks_setup", new=AsyncMock()) as mock_setup:
                    with patch("sau_cli.KSVideo", FakeVideoUploader):
                        asyncio.run(sau_cli.upload_kuaishou_video(request))

        mock_setup.assert_not_awaited()
        self.assertEqual(FakeVideoUploader.kwargs["account_file"], str(account_file))

    def test_kuaishou_note_upload_does_not_open_cookie_probe_before_publish(self):
        class FakeNoteUploader:
            kwargs = None

            def __init__(self, **kwargs):
                FakeNoteUploader.kwargs = kwargs

            async def main(self):
                return None

        with tempfile.TemporaryDirectory() as tmp_dir:
            account_file = Path(tmp_dir) / "kuaishou_creator.json"
            account_file.write_text("{}")
            request = sau_cli.KuaishouNoteUploadRequest(
                account_name="creator",
                image_files=[Path("demo.png")],
                title="图文标题",
                note="图文正文",
                tags=["测试"],
                publish_date=0,
            )

            with patch("sau_cli.resolve_account_file", return_value=account_file):
                with patch("sau_cli.ks_setup", new=AsyncMock()) as mock_setup:
                    with patch("sau_cli.KSNote", FakeNoteUploader):
                        asyncio.run(sau_cli.upload_kuaishou_note(request))

        mock_setup.assert_not_awaited()
        self.assertEqual(FakeNoteUploader.kwargs["account_file"], str(account_file))


if __name__ == "__main__":
    unittest.main()
