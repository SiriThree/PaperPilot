"""Private subprocess for offline CPU inference; no network or business persistence."""

import json
import sys


def main():
    resident = len(sys.argv) > 3 and sys.argv[3] == "--resident"
    try:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (3 * 1024**3, 3 * 1024**3))
            # Inference uses two CPU threads. CPU seconds accumulate across
            # threads; 60 CPU seconds killed valid long-paper imports after
            # roughly 30 wall seconds. The parent still enforces at most 90
            # wall seconds and kills this child on cancellation.
            if not resident:
                resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
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
        if resident:
            # Parent owns each request's wall deadline and kills/reaps on timeout.
            # A cumulative CPU limit would incorrectly kill a healthy long-lived worker.
            print(json.dumps({"ready": True}), flush=True)
            for line in sys.stdin.buffer:
                if len(line) > 64 * 1024:
                    return
                request = json.loads(line)
                try:
                    vector = list(model.query_embed([request["text"]]))[0]
                    response = {"id": request["id"], "vector": vector.tolist()}
                except Exception:
                    response = {"id": request.get("id"), "error": "unavailable"}
                print(json.dumps(response), flush=True)
            return
        texts = json.loads(sys.stdin.buffer.read(50 * 1024 * 1024))
        query = list(model.query_embed([texts[0]]))
        documents = list(model.passage_embed(texts[1:], batch_size=16)) if len(texts) > 1 else []
        sys.stdout.write(json.dumps([v.tolist() for v in query + documents]))
    except Exception:
        print(json.dumps({"ready": False}) if resident else "null", flush=True)


if __name__ == "__main__":
    main()
