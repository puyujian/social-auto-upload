# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import re
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import cv2
import requests


REQUEST_TIMEOUT = 30
CREATOR_BASE_URL = "https://creator.xiaohongshu.com"
EDITH_BASE_URL = "https://edith.xiaohongshu.com"
UPLOAD_DEFAULT_HOST = "ros-upload.xiaohongshu.com"
XHS_GROUP_LIST_API = "/api/im/web/nns/group_list"
CREATOR_XSEC_APPID = "ugc"
CREATOR_SIGN_VERSION = "4.3.2"
CREATOR_WEB_BUILD = "4.84.1"
TRANSCODE_MAX_RETRIES = 20
TRANSCODE_RETRY_DELAY_SECONDS = 3
XHS_PRIVACY_PUBLIC = 0
XHS_PRIVACY_PRIVATE = 1
STATIC_DIR = Path(__file__).resolve().parent / "static"


class XiaohongshuProtocolError(RuntimeError):
    """小红书协议链路的可读错误。"""


@dataclass(slots=True)
class MediaInfo:
    file_id: str
    file_size: int
    width: int = 0
    height: int = 0
    video_id: str = ""
    mime_type: str = "image/png"

    def as_reference_dict(self) -> dict[str, Any]:
        return {
            "fileIds": self.file_id,
            "file_size": self.file_size,
            "width": self.width,
            "height": self.height,
            "video_id": self.video_id,
            "mime_type": self.mime_type,
        }


class CreatorSigner:
    """按参考项目和 xhshow 的纯 Python 思路生成 Creator 侧签名。"""

    STANDARD_BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    CUSTOM_BASE64_ALPHABET = "ZmserbBoHQtNP+wOcza/LpngG8yJq42KWYj0DSfdikx3VT16IlUAFM97hECvuRX5"
    X3_BASE64_ALPHABET = "MfgqrsbcyzPQRStuvC7mn501HIJBo2DEFTKdeNOwxWXYZap89+/A4UVLhijkl63G"
    HEX_KEY = (
        "71a302257793271ddd273bcee3e4b98d9d7935e1da33f5765e2ea8afb6dc77a5"
        "1a499d23b67c20660025860cbf13d4540d92497f58686c574e508f46e195634"
        "4f39139bf4faf22a3eef120b79258145b2feb5193b6478669961298e79bedca"
        "646e1a693a926154a5a7a1bd1cf0dedb742f917a747a1e388b234f2277516"
        "db7116035439730fa61e9822a0eca7bff72d8"
    )
    VERSION_BYTES = [121, 104, 96, 41]
    HASH_IV = (1831565813, 461845907, 2246822507, 3266489909)
    A3_PREFIX = [2, 97, 51, 16]
    ENV_TABLE = [115, 248, 83, 102, 103, 201, 181, 131, 99, 94, 4, 68, 250, 132, 21]
    ENV_CHECKS_DEFAULT = [0, 1, 18, 1, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0]
    CHECKSUM_TABLE_POLY = 0xEDB88320

    def __init__(self, cookies: dict[str, str]):
        self.cookies = dict(cookies)
        self.a1 = str(self.cookies.get("a1") or "")
        if not self.a1:
            raise XiaohongshuProtocolError("小红书协议发布需要 cookie 中包含 a1，请先重新登录该账号")
        self._custom_encode_table = str.maketrans(self.STANDARD_BASE64_ALPHABET, self.CUSTOM_BASE64_ALPHABET)
        self._x3_encode_table = str.maketrans(self.STANDARD_BASE64_ALPHABET, self.X3_BASE64_ALPHABET)
        self._xs_common_secret = _load_creator_xs_common_secret()

    def sign_get(self, spliced_api: str) -> dict[str, str]:
        return self._sign("GET", spliced_api, "")

    def sign_post(self, api: str, data: dict[str, Any] | str | None = None) -> tuple[dict[str, str], str]:
        data_text = _compact_json(data) if data is not None else ""
        return self._sign("POST", api, data_text), data_text

    def _sign(self, method: str, api: str, data_text: str) -> dict[str, str]:
        full_string = api + (data_text if method.upper() == "POST" else "")
        xs = self._build_xs(api, full_string)
        xt = int(time.time() * 1000)
        return {
            "x-s": xs,
            "x-t": str(xt),
            "x-s-common": self._build_xs_common(xs, xt),
            "x-b3-traceid": _random_hex(16),
            "x-xray-traceid": _random_hex(32),
        }

    def _build_xs(self, api: str, content: str) -> str:
        content_md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
        api_md5 = hashlib.md5(api.encode("utf-8")).hexdigest()
        x3 = "mns0301_" + self._build_x3(content_md5, api_md5, content)
        sign_data = {
            "x0": CREATOR_SIGN_VERSION,
            "x1": CREATOR_XSEC_APPID,
            "x2": "Windows",
            "x3": x3,
            "x4": "object",
        }
        return "XYS_" + self._custom_b64(_compact_json(sign_data))

    def _build_x3(self, content_md5: str, api_md5: str, content: str) -> str:
        seed = random.randint(0, 0xFFFFFFFF)
        seed_byte = seed & 0xFF
        timestamp_ms = int(time.time() * 1000)
        effective_ts_ms = max(0, timestamp_ms - random.randint(10, 50) * 1000)

        payload: list[int] = []
        payload.extend(self.VERSION_BYTES)
        payload.extend(_int_to_le_bytes(seed, 4))
        ts_bytes = _int_to_le_bytes(timestamp_ms, 8)
        payload.extend(ts_bytes)
        payload.extend(_int_to_le_bytes(effective_ts_ms, 8))
        payload.extend(_int_to_le_bytes(random.randint(15, 50), 4))
        payload.extend(_int_to_le_bytes(random.randint(1000, 1200), 4))
        payload.extend(_int_to_le_bytes(len(content.encode("utf-8")), 4))

        content_md5_bytes = bytes.fromhex(content_md5)
        payload.extend([content_md5_bytes[index] ^ seed_byte for index in range(8)])

        a1_bytes = self.a1.encode("utf-8")[:52].ljust(52, b"\x00")
        payload.append(len(a1_bytes))
        payload.extend(a1_bytes)

        app_bytes = CREATOR_XSEC_APPID.encode("utf-8")[:10].ljust(10, b"\x00")
        payload.append(len(app_bytes))
        payload.extend(app_bytes)

        payload.extend([1, seed_byte ^ self.ENV_TABLE[0]])
        payload.extend([self.ENV_TABLE[index] ^ self.ENV_CHECKS_DEFAULT[index] for index in range(1, 15)])

        api_md5_bytes = [int(api_md5[index : index + 2], 16) for index in range(0, 32, 2)]
        payload.extend(self.A3_PREFIX + [value ^ seed_byte for value in self._custom_hash(ts_bytes + api_md5_bytes)])

        transformed = bytearray(len(payload))
        key = bytes.fromhex(self.HEX_KEY)
        for index, value in enumerate(payload):
            transformed[index] = (value ^ key[index]) & 0xFF if index < len(key) else value & 0xFF
        return self._x3_b64(transformed[:144])

    def _custom_hash(self, input_bytes: list[int]) -> list[int]:
        s0, s1, s2, s3 = self.HASH_IV
        length = len(input_bytes)
        s0 ^= length
        s1 ^= length << 8
        s2 ^= length << 16
        s3 ^= length << 24

        for index in range(length // 8):
            v0, v1 = struct.unpack("<II", bytes(input_bytes[index * 8 : (index + 1) * 8]))
            s0 = _rotate_left(((s0 + v0) & 0xFFFFFFFF) ^ s2, 7)
            s1 = _rotate_left(((v0 ^ s1) + s3) & 0xFFFFFFFF, 11)
            s2 = _rotate_left(((s2 + v1) & 0xFFFFFFFF) ^ s0, 13)
            s3 = _rotate_left(((s3 ^ v1) + s1) & 0xFFFFFFFF, 17)

        t0 = s0 ^ length
        t1 = s1 ^ t0
        t2 = (s2 + t1) & 0xFFFFFFFF
        t3 = s3 ^ t2

        rot_t0 = _rotate_left(t0, 9)
        rot_t1 = _rotate_left(t1, 13)
        rot_t2 = _rotate_left(t2, 17)
        rot_t3 = _rotate_left(t3, 19)

        s0 = (rot_t0 + rot_t2) & 0xFFFFFFFF
        s1 = rot_t1 ^ rot_t3
        s2 = (rot_t2 + s0) & 0xFFFFFFFF
        s3 = rot_t3 ^ s1

        result: list[int] = []
        for value in (s0, s1, s2, s3):
            result.extend(_int_to_le_bytes(value, 4))
        return result

    def _build_xs_common(self, xs: str, xt: int) -> str:
        md5_value = hashlib.md5(f"{xt}{xs}{self._xs_common_secret}".encode("utf-8")).hexdigest()
        sign_data = {
            "s0": 5,
            "s1": "",
            "x0": "1",
            "x1": CREATOR_SIGN_VERSION,
            "x2": "Windows",
            "x3": CREATOR_XSEC_APPID,
            "x4": CREATOR_WEB_BUILD,
            "x5": self.a1,
            "x6": xt,
            "x7": xs,
            "x8": self._xs_common_secret,
            "x9": _crc32_js_signed(bytes.fromhex(md5_value)),
            "x10": 0,
            "x11": "normal",
        }
        return self._custom_b64(_compact_json(sign_data))

    def _custom_b64(self, value: str | bytes | bytearray) -> str:
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        return base64.b64encode(raw).decode("utf-8").translate(self._custom_encode_table)

    def _x3_b64(self, value: bytes | bytearray) -> str:
        return base64.b64encode(bytes(value)).decode("utf-8").translate(self._x3_encode_table)


class XhsCreatorProtocol:
    def __init__(self, account_file: str | Path, *, session: requests.Session | None = None):
        self.account_file = Path(account_file)
        self.cookies = load_cookie_state(self.account_file)
        self.signer = CreatorSigner(self.cookies)
        self.session = session or requests.Session()

    def validate_login_state(self) -> dict[str, Any]:
        return self.request_upload_permit("image")

    def publish_video(
        self,
        *,
        video_file: str | Path,
        title: str,
        description: str,
        tags: list[str],
        group_chat: str = "",
        confirm_publish: bool = False,
    ) -> dict[str, Any]:
        video_bytes = Path(video_file).read_bytes()
        cover_bytes, metadata = extract_video_cover_and_metadata(video_bytes)
        video_info = self.upload_media(video_bytes, "video")
        cover_info = self.upload_media(cover_bytes, "image")
        if video_info.video_id:
            self.wait_transcode(video_info.video_id)
        payload = build_video_note_payload(
            title=title,
            description=description,
            file_info=video_info.as_reference_dict(),
            cover_info=cover_info.as_reference_dict(),
            metadata=metadata,
        )
        return self._post_note(payload, tags=tags, group_chat=group_chat, confirm_publish=confirm_publish)

    def publish_note(
        self,
        *,
        image_files: list[str | Path],
        title: str,
        note: str,
        tags: list[str],
        group_chat: str = "",
        confirm_publish: bool = False,
    ) -> dict[str, Any]:
        if not image_files:
            raise XiaohongshuProtocolError("小红书协议图文发布至少需要 1 张图片")
        file_infos = [self.upload_media(Path(path).read_bytes(), "image").as_reference_dict() for path in image_files]
        payload = build_image_note_payload(title=title, description=note, file_infos=file_infos)
        return self._post_note(payload, tags=tags, group_chat=group_chat, confirm_publish=confirm_publish)

    def request_upload_permit(self, media_type: str) -> dict[str, Any]:
        if media_type not in {"image", "video"}:
            raise XiaohongshuProtocolError(f"不支持的上传类型: {media_type}")
        api = "/api/media/v1/upload/creator/permit"
        params = {
            "biz_name": "spectrum",
            "scene": media_type,
            "file_count": "1",
            "version": "1",
            "source": "web",
        }
        spliced_api = splice_api(api, params)
        headers = creator_json_headers(host="creator.xiaohongshu.com", referer_target=media_type)
        headers.update(self.signer.sign_get(spliced_api))
        response = self.session.get(
            CREATOR_BASE_URL + spliced_api,
            headers=headers,
            cookies=self.cookies,
            timeout=REQUEST_TIMEOUT,
        )
        payload = parse_json_response(response, "获取小红书上传凭证失败")
        if not payload.get("success"):
            message = payload.get("msg") or payload.get("message") or "获取小红书上传凭证失败，请检查账号 cookie"
            raise XiaohongshuProtocolError(str(message))
        return payload

    def upload_media(self, file_bytes: bytes, media_type: str) -> MediaInfo:
        permit = self.request_upload_permit(media_type)
        try:
            upload_permit = permit["data"]["uploadTempPermits"][0]
            upload_host = upload_permit.get("uploadAddr") or UPLOAD_DEFAULT_HOST
            upload_url, host_for_signature = normalize_upload_host(str(upload_host))
            file_id = str(upload_permit["fileIds"][0]).split("/")[-1]
            expire_time = str(upload_permit["expireTime"])[:10]
            token = str(upload_permit["token"])
        except Exception as exc:
            raise XiaohongshuProtocolError("小红书上传凭证结构异常，无法继续协议上传") from exc

        xt = str(int(time.time() * 1000))[:10]
        message = f"{xt};{expire_time}"
        signature = build_ros_signature(message, file_id, len(file_bytes), host_for_signature)
        headers = upload_media_headers(message, signature, token)
        response = self.session.put(
            f"{upload_url}/spectrum/{file_id}",
            headers=headers,
            data=file_bytes,
            cookies=self.cookies,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        if media_type == "image":
            width, height = read_image_size(file_bytes)
            return MediaInfo(file_id=file_id, file_size=len(file_bytes), width=width, height=height)

        video_id = response.headers.get("X-Ros-Video-Id")
        if not video_id:
            raise XiaohongshuProtocolError("小红书视频上传成功但响应缺少 X-Ros-Video-Id")
        return MediaInfo(file_id=file_id, file_size=len(file_bytes), video_id=video_id)

    def wait_transcode(self, video_id: str) -> dict[str, Any]:
        last_payload: dict[str, Any] = {}
        for _ in range(TRANSCODE_MAX_RETRIES):
            payload = self.query_transcode(video_id)
            last_payload = payload
            data = payload.get("data") or {}
            if (
                data.get("hasFirstFrame") is True
                or data.get("has_first_frame") is True
                or data.get("firstFrameFileId")
                or data.get("first_frame_file_id")
                or data.get("status") in (2, "success", "SUCCESS")
                or not data
            ):
                return payload
            time.sleep(TRANSCODE_RETRY_DELAY_SECONDS)
        raise XiaohongshuProtocolError("小红书视频转码等待超时，协议发布已停止")

    def query_transcode(self, video_id: str) -> dict[str, Any]:
        api = "/web_api/sns/capa/postgw/query_transcode"
        params = {
            "video_id": str(video_id),
            "need_transcode": "false",
            "resource_type": "0",
        }
        spliced_api = splice_api(api, params)
        headers = edith_json_headers()
        headers.update(self.signer.sign_get(spliced_api))
        response = self.session.get(
            EDITH_BASE_URL + spliced_api,
            headers=headers,
            cookies=self.cookies,
            timeout=REQUEST_TIMEOUT,
        )
        payload = parse_json_response(response, "查询小红书视频转码状态失败")
        if not payload.get("success"):
            raise XiaohongshuProtocolError(str(payload.get("msg") or "查询小红书视频转码状态失败"))
        return payload

    def query_group_list(self, note_id: str = "") -> list[dict[str, Any]]:
        spliced_api = splice_api(XHS_GROUP_LIST_API, {"noteId": note_id})
        headers = edith_json_headers()
        headers.update(self.signer.sign_get(spliced_api))
        response = self.session.get(
            EDITH_BASE_URL + spliced_api,
            headers=headers,
            cookies=self.cookies,
            timeout=REQUEST_TIMEOUT,
        )
        payload = parse_json_response(response, "查询小红书群聊列表失败")
        if not payload.get("success"):
            raise XiaohongshuProtocolError(str(payload.get("msg") or "查询小红书群聊列表失败"))
        return extract_group_list(payload)

    def resolve_group_bind(self, group_chat: str) -> dict[str, Any]:
        return build_group_bind(select_group_for_bind(self.query_group_list(), group_chat), group_chat)

    def search_topic(self, topic: str) -> dict[str, str]:
        api = "/web_api/sns/v1/search/topic"
        data = {
            "keyword": topic,
            "suggest_topic_request": {
                "title": "",
                "desc": f"#{topic}",
            },
            "page": {
                "page_size": 20,
                "page": 1,
            },
        }
        headers, body = self._signed_post_headers(api, data)
        response = self.session.post(
            EDITH_BASE_URL + api,
            headers=headers,
            cookies=self.cookies,
            data=body.encode("utf-8"),
            timeout=REQUEST_TIMEOUT,
        )
        payload = parse_json_response(response, f"查询小红书话题失败: {topic}")
        if not payload.get("success"):
            raise XiaohongshuProtocolError(str(payload.get("msg") or f"查询小红书话题失败: {topic}"))
        topics = ((payload.get("data") or {}).get("topic_info_dtos") or [])
        if not topics:
            raise XiaohongshuProtocolError(f"未找到小红书话题: {topic}")
        first = topics[0]
        return {
            "id": first["id"],
            "link": first["link"],
            "name": first["name"],
            "type": "topic",
        }

    def _post_note(
        self,
        payload: dict[str, Any],
        *,
        tags: list[str],
        group_chat: str = "",
        confirm_publish: bool,
    ) -> dict[str, Any]:
        for tag in tags:
            topic = self.search_topic(tag)
            payload["common"]["hash_tag"].append(topic)
            payload["common"]["desc"] += f" #{topic['name']}[话题]# "

        if str(group_chat or "").strip():
            inject_group_bind(payload, self.resolve_group_bind(group_chat))

        if not confirm_publish:
            raise XiaohongshuProtocolError(
                "协议上传前置链路已完成，但真实发布会公开笔记；请在明确确认后追加 --confirm-protocol-publish 再执行"
            )

        api = "/web_api/sns/v2/note"
        headers, body = self._signed_post_headers(api, payload)
        headers["x-rap-param"] = generate_x_rap_param(api, body)
        response = self.session.post(
            EDITH_BASE_URL + api,
            headers=headers,
            cookies=self.cookies,
            data=body.encode("utf-8"),
            timeout=REQUEST_TIMEOUT,
        )
        result = parse_json_response(response, "小红书协议发布失败")
        if not result.get("success"):
            raise XiaohongshuProtocolError(str(result.get("msg") or "小红书协议发布失败"))
        return result

    def _signed_post_headers(self, api: str, data: dict[str, Any] | str) -> tuple[dict[str, str], str]:
        headers = edith_json_headers()
        signed_headers, body = self.signer.sign_post(api, data)
        headers.update(signed_headers)
        return headers, body


def load_cookie_state(account_file: str | Path) -> dict[str, str]:
    path = Path(account_file)
    if not path.exists():
        raise XiaohongshuProtocolError(f"cookie文件不存在，请先完成小红书登录: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise XiaohongshuProtocolError(f"无法读取小红书 cookie 文件: {path}") from exc

    cookies: dict[str, str] = {}
    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        for item in raw["cookies"]:
            if isinstance(item, dict) and item.get("name") is not None:
                cookies[str(item["name"])] = str(item.get("value") or "")
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name") is not None:
                cookies[str(item["name"])] = str(item.get("value") or "")
    elif isinstance(raw, dict):
        cookies = {str(key): str(value) for key, value in raw.items() if isinstance(value, str)}
    elif isinstance(raw, str):
        cookies = parse_cookie_string(raw)

    if not cookies.get("a1"):
        raise XiaohongshuProtocolError("小红书协议发布需要 cookie 中包含 a1，请先重新登录该账号")
    return cookies


def parse_cookie_string(value: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(value or "").split(";"):
        if "=" not in part:
            continue
        name, cookie_value = part.split("=", 1)
        name = name.strip()
        if name:
            cookies[name] = cookie_value.strip()
    return cookies


def build_ros_signature(message: str, file_id: str, content_length: int, host: str) -> str:
    key = hmac.new(b"null", message.encode("utf-8"), hashlib.sha1).hexdigest()
    params_hash = hashlib.sha1(
        f"put\n/spectrum/{file_id}\n\ncontent-length={content_length}&host={host}\n".encode("utf-8")
    ).hexdigest()
    return hmac.new(
        key.encode("utf-8"),
        f"sha1\n{message}\n{params_hash}\n".encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()


def generate_x_rap_param(api: str, data: str) -> str:
    script_path = STATIC_DIR / "xhs_rap.js"
    if not script_path.exists():
        raise XiaohongshuProtocolError(f"缺少小红书 x-rap 签名脚本: {script_path}")
    node_script = r"""
const fs = require('fs');
const vm = require('vm');
const { TextEncoder, TextDecoder } = require('util');
const scriptPath = process.argv[1];
const api = process.argv[2];
const data = process.argv[3] || '';
const sandbox = {
  console: { log(){}, error(){}, warn(){}, info(){}, debug(){} },
  require,
  module: {},
  exports: {},
  setTimeout,
  clearTimeout,
  TextEncoder,
  TextDecoder,
  Buffer,
  URL,
  URLSearchParams,
  crypto: require('crypto').webcrypto,
  atob: value => Buffer.from(value, 'base64').toString('binary'),
  btoa: value => Buffer.from(value, 'binary').toString('base64')
};
sandbox.global = sandbox;
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), sandbox, { filename: scriptPath });
process.stdout.write(String(sandbox.generate_x_rap_param(api, data)));
"""
    result = subprocess.run(
        ["node", "-e", node_script, str(script_path), api, data],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip().splitlines()
        detail = message[-1] if message else "Node x-rap 签名执行失败"
        raise XiaohongshuProtocolError(f"生成小红书 x-rap-param 失败: {detail}")
    value = result.stdout.strip()
    if not value:
        raise XiaohongshuProtocolError("生成小红书 x-rap-param 失败: 签名结果为空")
    return value


def build_image_note_payload(*, title: str, description: str, file_infos: list[dict[str, Any]]) -> dict[str, Any]:
    images = []
    for file_info in file_infos:
        images.append(
            {
                "file_id": f"spectrum/{file_info['fileIds']}",
                "width": file_info.get("width") or 0,
                "height": file_info.get("height") or 0,
                "metadata": {"source": -1},
                "stickers": {"version": 2, "floating": []},
                "extra_info_json": _compact_json(
                    {
                        "mimeType": file_info.get("mime_type", "image/png"),
                        "image_metadata": {
                            "bg_color": "",
                            "origin_size": (file_info.get("file_size") or 0) / 1024,
                        },
                    }
                ),
            }
        )
    return {
        "common": common_payload("normal", title, description),
        "image_info": {"images": images},
        "video_info": None,
    }


def build_video_note_payload(
    *,
    title: str,
    description: str,
    file_info: dict[str, Any],
    cover_info: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    video_meta = metadata.get("video") or {}
    audio_meta = metadata.get("audio") or {}
    video_file_id = f"spectrum/{file_info['fileIds']}"
    cover_file_id = f"spectrum/{cover_info['fileIds']}"
    return {
        "common": common_payload("video", title, description),
        "image_info": None,
        "video_info": {
            "fileid": video_file_id,
            "file_id": video_file_id,
            "format_width": video_meta.get("width") or file_info.get("width") or 0,
            "format_height": video_meta.get("height") or file_info.get("height") or 0,
            "video_preview_type": "",
            "composite_metadata": {"video": video_meta, "audio": audio_meta},
            "timelines": [],
            "cover": {
                "fileid": cover_file_id,
                "file_id": cover_file_id,
                "height": cover_info.get("height") or video_meta.get("height") or 0,
                "width": cover_info.get("width") or video_meta.get("width") or 0,
                "frame": {"ts": 0, "is_user_select": False, "is_upload": False},
                "stickers": {"version": 2, "neptune": []},
                "fonts": [],
                "extra_info_json": "{}",
            },
            "chapters": [],
            "chapter_sync_text": False,
            "segments": {
                "count": 1,
                "need_slice": False,
                "items": [
                    {
                        "idx": 0,
                        "file_id": video_file_id,
                        "duration": round((video_meta.get("duration") or 0) / 1000, 3),
                    }
                ],
            },
        },
    }


def extract_group_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    source = data if isinstance(data, dict) else payload
    groups = source.get("group_list") or source.get("groupList") or []
    if not isinstance(groups, list):
        raise XiaohongshuProtocolError("小红书群聊列表结构异常，无法继续协议发布")
    return [group for group in groups if isinstance(group, dict)]


def select_group_for_bind(groups: list[dict[str, Any]], group_chat: str) -> dict[str, Any]:
    wanted = str(group_chat or "").strip()
    if not wanted:
        raise XiaohongshuProtocolError("小红书群聊名称为空，无法关联群聊")
    if not groups:
        raise XiaohongshuProtocolError("当前账号暂无可关联群聊，请先在小红书 App 端创建群聊")

    exact_matches = [group for group in groups if _group_name(group) == wanted]
    if len(exact_matches) == 1:
        selected = exact_matches[0]
    elif exact_matches:
        raise XiaohongshuProtocolError(f"小红书群聊名称不唯一: {wanted}")
    else:
        partial_matches = [group for group in groups if wanted in _group_name(group)]
        if len(partial_matches) == 1:
            selected = partial_matches[0]
        elif partial_matches:
            names = "、".join(_group_name(group) or "(未命名)" for group in partial_matches)
            raise XiaohongshuProtocolError(f"小红书群聊名称不唯一: {wanted}，候选: {names}")
        else:
            names = "、".join(_group_name(group) or "(未命名)" for group in groups)
            raise XiaohongshuProtocolError(f"未找到小红书群聊: {wanted}，当前可选: {names}")

    if selected.get("linkable") is False:
        raise XiaohongshuProtocolError(f"群聊不可关联: {_group_name(selected) or wanted}")
    return selected


def build_group_bind(group: dict[str, Any], requested_name: str = "") -> dict[str, Any]:
    group_id = str(group.get("groupId") or group.get("group_id") or "").strip()
    group_name = str(group.get("groupName") or group.get("group_name") or "").strip()
    if not group_id or not group_name:
        raise XiaohongshuProtocolError(f"小红书群聊字段缺失，无法关联: {requested_name or group_name or group_id}")
    return {
        "groupId": group_id,
        "groupName": group_name,
        "desc": str(group.get("desc") or ""),
        "avatar": str(group.get("avatar") or ""),
    }


def inject_group_bind(payload: dict[str, Any], group_bind: dict[str, Any]) -> None:
    common = payload.setdefault("common", {})
    raw_binds = common.get("business_binds") or "{}"
    try:
        business_binds = json.loads(raw_binds) if isinstance(raw_binds, str) else dict(raw_binds)
    except Exception as exc:
        raise XiaohongshuProtocolError("小红书 business_binds 结构异常，无法写入群聊绑定") from exc
    business_binds["groupBind"] = group_bind
    common["business_binds"] = _compact_json(business_binds)


def _group_name(group: dict[str, Any]) -> str:
    return str(group.get("groupName") or group.get("group_name") or "").strip()


def common_payload(note_type: str, title: str, description: str) -> dict[str, Any]:
    return {
        "type": note_type,
        "title": title,
        "note_id": "",
        "desc": description,
        "source": "{\"type\":\"web\",\"ids\":\"\",\"extraInfo\":\"{\\\"subType\\\":\\\"official\\\",\\\"systemId\\\":\\\"web\\\"}\"}",
        "business_binds": "{\"version\":1,\"noteId\":0,\"bizType\":0,\"noteOrderBind\":{},\"notePostTiming\":{},\"noteCollectionBind\":{\"id\":\"\"},\"noteSketchCollectionBind\":{\"id\":\"\"},\"coProduceBind\":{\"enable\":true},\"noteCopyBind\":{\"copyable\":true},\"interactionPermissionBind\":{\"commentPermission\":0},\"optionRelationList\":[]}",
        "ats": [],
        "hash_tag": [],
        "post_loc": {},
        "privacy_info": {"op_type": 1, "type": XHS_PRIVACY_PUBLIC, "user_ids": []},
        "goods_info": {},
        "biz_relations": [],
        "capa_trace_info": {
            "contextJson": "{\"recommend_title\":{\"recommend_title_id\":\"\",\"is_use\":3,\"used_index\":-1},\"recommendTitle\":[],\"recommend_topics\":{\"used\":[]}}"
        },
    }


def read_image_size(file_bytes: bytes) -> tuple[int, int]:
    import numpy as np

    image = cv2.imdecode(np.frombuffer(file_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise XiaohongshuProtocolError("小红书协议上传图片解码失败")
    height, width = image.shape[:2]
    if width > 2 * height:
        height = int(width / 2)
    return width, height


def extract_video_cover_and_metadata(video_bytes: bytes) -> tuple[bytes, dict[str, Any]]:
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_file:
            temp_file.write(video_bytes)
            temp_path = temp_file.name

        cap = cv2.VideoCapture(temp_path)
        if not cap.isOpened():
            raise XiaohongshuProtocolError("小红书协议发布无法读取视频文件")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration_ms = int(frame_count / fps * 1000) if fps else 0
        success, frame = cap.read()
        cap.release()
        if not success:
            raise XiaohongshuProtocolError("小红书协议发布无法提取视频首帧封面")
        ok, encoded = cv2.imencode(".jpg", frame)
        if not ok:
            raise XiaohongshuProtocolError("小红书协议发布视频封面编码失败")
        metadata = {
            "video": {
                "bitrate": None,
                "colour_primaries": "BT.709",
                "duration": duration_ms,
                "format": "AVC",
                "frame_rate": round(fps, 3) if fps else 0,
                "height": height,
                "matrix_coefficients": "BT.709",
                "rotation": 0,
                "transfer_characteristics": "BT.709",
                "width": width,
            },
            "audio": {
                "bitrate": None,
                "channels": 2,
                "duration": duration_ms,
                "format": "AAC",
                "sampling_rate": 48000,
            },
        }
        return encoded.tobytes(), metadata
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


def creator_json_headers(*, host: str, referer_target: str) -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "authorization": "",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": f"https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target={referer_target}",
        "sec-ch-ua": "\"Not)A;Brand\";v=\"8\", \"Chromium\";v=\"138\", \"Microsoft Edge\";v=\"138\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": user_agent(),
        "Host": host,
    }


def edith_json_headers() -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "authorization": "",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "origin": "https://creator.xiaohongshu.com",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://creator.xiaohongshu.com/",
        "sec-ch-ua": "\"Not)A;Brand\";v=\"8\", \"Chromium\";v=\"138\", \"Microsoft Edge\";v=\"138\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": user_agent(),
    }


def upload_media_headers(message: str, signature: str, token: str) -> dict[str, str]:
    return {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "authorization": (
            "q-sign-algorithm=sha1&q-ak=null"
            f"&q-sign-time={message}&q-key-time={message}"
            f"&q-header-list=content-length;host&q-url-param-list=&q-signature={signature}"
        ),
        "cache-control": "",
        "content-type": "",
        "origin": "https://creator.xiaohongshu.com",
        "pragma": "no-cache",
        "referer": "https://creator.xiaohongshu.com/",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Microsoft Edge";v="122"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": user_agent(),
        "x-cos-security-token": token,
    }


def parse_json_response(response: requests.Response, action: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise XiaohongshuProtocolError(action) from exc


def normalize_upload_host(upload_host: str) -> tuple[str, str]:
    value = str(upload_host or "").strip()
    if not value:
        value = UPLOAD_DEFAULT_HOST
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        return value.rstrip("/"), parsed.netloc
    return f"https://{value}", value


def splice_api(api: str, params: dict[str, Any]) -> str:
    normalized = {key: "" if value is None else value for key, value in params.items()}
    return api + "?" + urlencode(normalized, doseq=True)


def _compact_json(data: dict[str, Any] | list[Any] | str) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _load_creator_xs_common_secret() -> str:
    script_path = STATIC_DIR / "xhs_creator_260411.js"
    if not script_path.exists():
        raise XiaohongshuProtocolError(f"缺少小红书 Creator 签名脚本: {script_path}")
    text = script_path.read_text(encoding="utf-8")
    match = re.search(r'var\s+fff\s*=\s*"([^"]+)"', text)
    if not match:
        raise XiaohongshuProtocolError("小红书 Creator 签名脚本缺少 x-s-common 密钥")
    return match.group(1)


def _random_hex(length: int) -> str:
    return "".join(random.choice("abcdef0123456789") for _ in range(length))


def _int_to_le_bytes(value: int, length: int) -> list[int]:
    result: list[int] = []
    for _ in range(length):
        result.append(value & 0xFF)
        value >>= 8
    return result


def _rotate_left(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (32 - bits))) & 0xFFFFFFFF


def _crc32_js_signed(data: bytes) -> int:
    crc = -1
    table = []
    for item in range(255, -1, -1):
        value = item
        for _ in range(8):
            value = ((value >> 1) ^ CreatorSigner.CHECKSUM_TABLE_POLY) if value & 1 else value >> 1
        table.insert(0, value & 0xFFFFFFFF)
    for byte in data:
        crc = table[(crc & 255) ^ byte] ^ ((crc & 0xFFFFFFFF) >> 8)
    value = ((-1 & 0xFFFFFFFF) ^ (crc & 0xFFFFFFFF) ^ CreatorSigner.CHECKSUM_TABLE_POLY) & 0xFFFFFFFF
    return value - 0x100000000 if value > 0x7FFFFFFF else value


def user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0"
    )
