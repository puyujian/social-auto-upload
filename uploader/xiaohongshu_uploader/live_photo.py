"""将 GIF 动画转换为实况图的 JPEG 封面和 MP4 动态片段。"""
import io
import math
from pathlib import Path
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageSequence


def image_to_live_photo(content: bytes) -> tuple[bytes, bytes]:
    """制作 2 秒、30 fps 的轻微环绕运镜，画面内容完整保留。"""
    with Image.open(io.BytesIO(content)) as image:
        rgba = image.convert("RGBA")
        size = (image.width + image.width % 2, image.height + image.height % 2)
        rgb = Image.new("RGB", size, "white")
        rgb.paste(rgba, (0, 0), rgba.getchannel("A"))
    pixels = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
    background = cv2.GaussianBlur(pixels, (0, 0), sigmaX=12)
    width, height = size
    cover = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="xhs-live-photo-") as directory:
        path = Path(directory) / "animation.mp4"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, size)
        if not writer.isOpened():
            writer.release()
            raise RuntimeError("实况图生成失败：MP4 编码器不可用")
        try:
            for index in range(60):
                angle = 2 * math.pi * index / 60
                transform = np.float32([
                    [0.97, 0, width * (0.015 + 0.01 * math.cos(angle))],
                    [0, 0.97, height * (0.015 + 0.01 * math.sin(angle))],
                ])
                frame = cv2.warpAffine(
                    pixels, transform, size, dst=background.copy(),
                    flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT,
                )
                if index == 0:
                    Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).save(
                        cover, format="JPEG", quality=95,
                    )
                writer.write(frame)
        finally:
            writer.release()
        return cover.getvalue(), path.read_bytes()


def gif_to_live_photo(content: bytes) -> tuple[bytes, bytes]:
    fps = 30
    cover = io.BytesIO()
    with Image.open(io.BytesIO(content)) as image, tempfile.TemporaryDirectory(
        prefix="xhs-live-photo-"
    ) as directory:
        size = (image.width + image.width % 2, image.height + image.height % 2)
        path = Path(directory) / "animation.mp4"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
        if not writer.isOpened():
            writer.release()
            raise RuntimeError("GIF 转实况图失败：MP4 编码器不可用")
        elapsed_ms = 0
        written = 0
        try:
            for index, frame in enumerate(ImageSequence.Iterator(image)):
                rgba = frame.convert("RGBA")
                rgb = Image.new("RGB", size, "white")
                rgb.paste(rgba, (0, 0), rgba.getchannel("A"))
                if index == 0:
                    rgb.save(cover, format="JPEG", quality=95)
                pixels = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
                elapsed_ms += frame.info.get("duration", 100) or 100
                target = max(1, round(elapsed_ms * fps / 1000))
                while written < target:
                    writer.write(pixels)
                    written += 1
        finally:
            writer.release()
        return cover.getvalue(), path.read_bytes()
