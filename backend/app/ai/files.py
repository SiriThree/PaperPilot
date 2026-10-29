import asyncio
import json
import os
import stat
import sys
from pathlib import Path

from app.errors import AppError

MAX_BYTES = 20 * 1024 * 1024


class LocalPDFReader:
    """Trusted local upload root; only B's canonical <paper UUID>.pdf keys are readable."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _read(self, file):
        if file.storage_key != f"{file.paper_id}.pdf":
            raise AppError(422, "INVALID_FILE_REFERENCE", "文件引用格式不正确")
        path = self.root / file.storage_key
        try:
            # O_NOFOLLOW also closes the check/open symlink race on supported platforms.
            if path.is_symlink():
                raise OSError("symlink")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise OSError("not a regular file")
                if info.st_size > MAX_BYTES:
                    raise AppError(413, "PDF_LIMIT_EXCEEDED", "PDF 超过解析限制")
                payload = stream.read(MAX_BYTES + 1)
                if len(payload) > MAX_BYTES:
                    raise AppError(413, "PDF_LIMIT_EXCEEDED", "PDF 超过解析限制")
                return payload
        except OSError:
            raise AppError(503, "FILE_UNAVAILABLE", "论文文件暂不可读取") from None

    async def chunks(self, file, settings, *, title=""):
        payload = await asyncio.to_thread(self._read, file)
        options = []
        if settings.active_chunk_strategy == "sentence-token-v2":
            options = [
                json.dumps(
                    {
                        "tokenizer_dir": str(settings.local_model_dir.resolve()),
                        "target_tokens": settings.chunk_tokens,
                        "overlap_tokens": settings.chunk_token_overlap,
                        "title": title,
                    }
                )
            ]
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).with_name("pdf_worker.py")),
                str(settings.chunk_chars),
                str(settings.chunk_overlap),
                *options,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                    "TOKENIZERS_PARALLELISM": "false",
                    "HF_HUB_OFFLINE": "1",
                },
            )
        except OSError:
            raise AppError(503, "PDF_PARSER_UNAVAILABLE", "PDF 解析器暂不可用") from None
        try:
            output, _ = await process.communicate(payload)
            try:
                result = json.loads(output)
            except (ValueError, UnicodeError):
                raise AppError(422, "PDF_INVALID", "PDF 无法解析或超过解析资源限制") from None
            if "error" in result:
                code = result["error"]
                messages = {
                    "PDF_ENCRYPTED": "暂不支持加密 PDF",
                    "PDF_NO_TEXT": "PDF 没有可提取文本，扫描件需要 OCR",
                    "PDF_LIMIT_EXCEEDED": "PDF 超过解析限制",
                    "PDF_INVALID": "PDF 格式无效或无法解析",
                    "AI_CHUNK_INPUT_LIMIT": "论文标题或原文单元超出嵌入切块预算",
                }
                raise AppError(422, code, messages.get(code, "PDF 无法解析"))
            return result["chunks"]
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
