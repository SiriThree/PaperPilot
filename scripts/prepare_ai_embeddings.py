"""Explicitly download a supported local model; app startup never downloads weights."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--cache-dir", type=Path, default=Path(".runtime/ai-models"))
    parser.add_argument("--output", type=Path, default=Path(".runtime/ai-model.json"))
    args = parser.parse_args()
    try:
        from fastembed import TextEmbedding

        description = next(m for m in TextEmbedding.list_supported_models() if m["model"] == args.model)
        model = TextEmbedding(model_name=args.model, cache_dir=str(args.cache_dir.resolve()), lazy_load=True)
        root = Path(model.model._model_dir).resolve()
        files = {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*")
            if p.is_file() and not p.name.startswith(".")
        }
        manifest = {
            "model": args.model,
            "dimensions": description["dim"],
            "model_dir": str(root),
            "source": description["sources"],
            "files_sha256": files,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"model": args.model, "dimensions": description["dim"], "model_dir": str(root)}))
    except Exception:
        parser.exit(1, "Model preparation failed; check the model name, optional dependencies and network.\n")


if __name__ == "__main__":
    main()
