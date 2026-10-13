"""Durable acknowledgement for repeated calls to the final public publish API."""
import hashlib
import json
import os
from pathlib import Path


class PublishReceipt:
    def __init__(self, account_file, key):
        digest = hashlib.sha256((str(Path(account_file).resolve()) + '\n' + key).encode()).hexdigest()
        self.path = Path(account_file).parent / '.publish-receipts' / (digest + '.json')

    def cached(self):
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise RuntimeError('发布回执不完整，结果待确认，避免重复发帖') from None
        if value.get('state') == 'success':
            return value['result']
        raise RuntimeError('发布结果待确认：同一任务已提交过最终发布请求，等待核实回执，避免重复发帖')

    def claim(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return self.cached()
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'state': 'submitting'}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        return None

    def confirm(self, result):
        temporary = self.path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'state': 'success', 'result': result}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def reject(self):
        self.path.unlink(missing_ok=True)
