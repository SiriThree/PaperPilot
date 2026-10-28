"""Private subprocess for offline CPU inference; no network or business persistence."""

import json
import sys


def main():
    try:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
            resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        except (ImportError, ValueError, OSError):
            pass
        from fastembed import TextEmbedding

        model = TextEmbedding(
            model_name=sys.argv[1],
            specific_model_path=sys.argv[2],
            local_files_only=True,
            threads=2,
            providers=["CPUExecutionProvider"],
        )
        texts = json.loads(sys.stdin.buffer.read(50 * 1024 * 1024))
        query = list(model.query_embed([texts[0]]))
        documents = list(model.passage_embed(texts[1:], batch_size=16)) if len(texts) > 1 else []
        sys.stdout.write(json.dumps([v.tolist() for v in query + documents]))
    except Exception:
        sys.stdout.write("null")


if __name__ == "__main__":
    main()
