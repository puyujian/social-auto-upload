import base64
import json
import types
import unittest

from utils import proxy


class ProxyConfigTests(unittest.TestCase):
    def test_normalize_playwright_proxy_accepts_socks5h_and_uses_socks5_for_context(self):
        result = proxy.normalize_playwright_proxy(
            server="socks5h://127.0.0.1:1080",
            username="user",
            password="pass",
        )

        self.assertEqual(
            result,
            {
                "server": "socks5://127.0.0.1:1080",
                "username": "user",
                "password": "pass",
            },
        )

    def test_normalize_playwright_proxy_rejects_server_without_supported_scheme(self):
        with self.assertRaisesRegex(ValueError, "代理 server"):
            proxy.normalize_playwright_proxy(server="127.0.0.1:7890")

    def test_proxy_from_args_normalizes_cli_proxy(self):
        args = types.SimpleNamespace(
            proxy_server="socks5h://proxy.local:1080",
            proxy_username="name",
            proxy_password="secret",
        )

        result = proxy.proxy_from_args(args)

        self.assertEqual(result["server"], "socks5://proxy.local:1080")
        self.assertEqual(result["username"], "name")
        self.assertEqual(result["password"], "secret")

    def test_decode_playwright_proxy_reads_base64_json(self):
        raw = json.dumps(
            {"server": "http://127.0.0.1:7890", "username": "u", "password": "p"},
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")

        result = proxy.decode_playwright_proxy(encoded)

        self.assertEqual(
            result,
            {"server": "http://127.0.0.1:7890", "username": "u", "password": "p"},
        )

    def test_merge_proxy_context_options_uses_env_when_explicit_proxy_missing(self):
        encoded = proxy.encode_playwright_proxy({"server": "socks5h://proxy.local:1080"})

        result = proxy.merge_proxy_context_options(
            {"storage_state": "account.json"},
            env={proxy.SAU_PLAYWRIGHT_PROXY_ENV: encoded},
        )

        self.assertEqual(result["storage_state"], "account.json")
        self.assertEqual(result["proxy"], {"server": "socks5://proxy.local:1080"})

    def test_merge_proxy_context_options_prefers_explicit_proxy_over_env(self):
        encoded = proxy.encode_playwright_proxy({"server": "http://env.local:7890"})

        result = proxy.merge_proxy_context_options(
            {"storage_state": "account.json"},
            proxy={"server": "http://cli.local:7890"},
            env={proxy.SAU_PLAYWRIGHT_PROXY_ENV: encoded},
        )

        self.assertEqual(result["proxy"], {"server": "http://cli.local:7890"})


if __name__ == "__main__":
    unittest.main()
