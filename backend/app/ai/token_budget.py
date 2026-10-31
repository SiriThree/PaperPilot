"""Offline token counting matching the prepared FastEmbed tokenizer, without ONNX."""

import hashlib
import json
from pathlib import Path


class TokenBudget:
    def __init__(self, directory: Path):
        from tokenizers import AddedToken, Tokenizer

        directory = directory.resolve(strict=True)
        names = ["tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"]
        contents = {name: (directory / name).read_bytes() for name in names if (directory / name).is_file()}
        config = json.loads(contents["tokenizer_config.json"])
        limits = [config.get(key) for key in ("model_max_length", "max_length")]
        limits = [value for value in limits if type(value) is int and 0 < value <= 1_000_000]
        if not limits:
            raise ValueError("Prepared tokenizer has no usable input limit")
        self.max_tokens = min(limits)
        self.tokenizer = Tokenizer.from_str(contents["tokenizer.json"].decode("utf-8"))
        for value in json.loads(contents.get("special_tokens_map.json", b"{}")).values():
            for token in value if isinstance(value, list) else [value]:
                if isinstance(token, (dict, str)):
                    self.tokenizer.add_special_tokens(
                        [AddedToken(**token) if isinstance(token, dict) else token]
                    )
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()
        digest = hashlib.sha256()
        for name, body in sorted(contents.items()):
            digest.update(name.encode() + b"\0" + body + b"\0")
        self.fingerprint = digest.hexdigest()

    def count(self, text, *, special=True):
        return len(self.tokenizer.encode(text, add_special_tokens=special).ids)

    def offsets(self, text):
        return self.tokenizer.encode(text, add_special_tokens=False).offsets
