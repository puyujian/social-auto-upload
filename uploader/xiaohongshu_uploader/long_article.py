"""Local long-article CLI entrypoint; no browser, account cookies or network."""
import asyncio
import json
from uploader.xiaohongshu_uploader.original_article_renderer import render_original_article

async def generate_article(account_file, input_path, output_dir, *, headless=True):
    data = json.loads(input_path.read_text(encoding='utf-8'))
    return await render_original_article(data, output_dir)
