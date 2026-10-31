"""Private scoped reads for C: streaming documents and exact pgvector Top-K.

All predicates run before ranking. Only active root sources participate; immutable
historical citations remain readable through the existing source APIs.
"""

import json
import math
import time
from uuid import uuid4

from app.data import mapping
from app.errors import AppError
from app.indexing import EmbeddingModel


class RetrievalSnapshot:
    def __init__(self, connection, ctx, query, paper_ids, timeout_seconds):
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 90:
            raise ValueError("Invalid retrieval timeout")
        self.connection = connection
        self.ctx = ctx
        self.paper_ids = None if paper_ids is None else list(paper_ids)
        self.deadline = time.monotonic() + timeout_seconds
        self.values = (
            ctx.project_id,
            query.year_from,
            query.year_from,
            query.year_to,
            query.year_to,
            self.paper_ids,
            self.paper_ids,
        )
        self.papers_sql = (
            "SELECT pp.paper_id,pp.project_id,p.title,p.year,pp.status,"
            " pp.active_process_task_id,pp.index_state FROM project_papers pp"
            " JOIN papers p ON p.metadata_id=pp.metadata_id AND p.project_id=pp.project_id"
            " WHERE pp.project_id=%s AND pp.removed_at IS NULL AND pp.status='searchable'"
            " AND (%s::int IS NULL OR p.year >= %s) AND (%s::int IS NULL OR p.year <= %s)"
            " AND (%s::uuid[] IS NULL OR pp.paper_id=ANY(%s::uuid[]))"
        )
        self.active_sql = (
            "WITH eligible AS (" + self.papers_sql + ")"
            " SELECT pp.*,rs.citation_id,rs.chunk_id,rs.page_number,rs.excerpt,rs.synthetic"
            " FROM eligible pp JOIN result_sources rs ON rs.project_id=pp.project_id"
            " AND rs.paper_id=pp.paper_id AND rs.task_id=pp.active_process_task_id AND rs.scope='root'"
        )

    def check_deadline(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AppError(504, "AI_TIMEOUT", "检索处理超时，请稍后重试")
        return max(1, int(remaining * 1000))

    def _budget(self):
        self.connection.execute(
            "SELECT set_config('statement_timeout',%s,true)", (str(self.check_deadline()),)
        )

    def check_papers(self):
        self._budget()
        if self.paper_ids is not None:
            rows = self.connection.execute(
                "SELECT paper_id FROM project_papers WHERE project_id=%s"
                " AND paper_id=ANY(%s::uuid[]) AND removed_at IS NULL",
                (self.ctx.project_id, self.paper_ids),
            ).fetchall()
            if {r["paper_id"] for r in rows} != set(self.paper_ids):
                raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")

    @staticmethod
    def key(row):
        return row["paper_id"], row["page_number"], row["chunk_id"]

    def documents(self):
        """A real server cursor: a fetchall/client cursor would buffer the corpus."""
        self._budget()
        with self.connection.cursor(name="retrieval_" + uuid4().hex) as cursor:
            cursor.itersize = 256
            cursor.execute(self.active_sql, self.values)
            for row in cursor:
                self.check_deadline()
                yield self.key(row), row["title"] + "\n" + row["excerpt"]

    def lexical_versions(self):
        """Only current, authorized paper versions; titles participate in lexical identity."""
        self._budget()
        rows = self.connection.execute(self.papers_sql + " ORDER BY pp.paper_id", self.values).fetchall()
        return [
            (row["project_id"], row["paper_id"], row["active_process_task_id"], row["title"])
            for row in rows
            if row["active_process_task_id"] is not None
        ]

    def lexical_documents(self, paper_ids):
        """Read cache misses once, ordered for per-paper incremental index construction."""
        if not paper_ids:
            return
        self._budget()
        with self.connection.cursor(name="lexical_" + uuid4().hex) as cursor:
            cursor.itersize = 256
            cursor.execute(
                "SELECT * FROM (" + self.active_sql + ") a WHERE paper_id=ANY(%s::uuid[])"
                " ORDER BY paper_id,page_number,chunk_id",
                self.values + (list(paper_ids),),
            )
            for row in cursor:
                self.check_deadline()
                yield self.key(row), row["title"] + "\n" + row["excerpt"]

    def nearest(self, model, vector, threshold, limit):
        model = EmbeddingModel.model_validate(model)
        if (
            len(vector) != model.dimensions
            or any(type(v) not in (float, int) or not math.isfinite(v) for v in vector)
            or not math.isclose(math.hypot(*vector), 1, rel_tol=1e-5)
            or not math.isfinite(threshold)
            or not -1 <= threshold <= 1
            or type(limit) is not int
            or not 1 <= limit <= 10000
        ):
            raise AppError(502, "AI_INVALID_RESPONSE", "查询向量或检索预算无效")
        prefix = "WITH active AS (" + self.active_sql + ")"
        self._budget()
        identity = self.connection.execute(
            "SELECT model_id FROM embedding_models WHERE project_id=%s AND provider=%s"
            " AND model_name=%s AND revision=%s AND dimensions=%s AND distance_metric=%s",
            (
                self.ctx.project_id,
                model.provider,
                model.model_name,
                model.revision,
                model.dimensions,
                model.distance_metric,
            ),
        ).fetchone()
        if identity is None:
            if self.connection.execute(prefix + " SELECT 1 FROM active LIMIT 1", self.values).fetchone():
                raise AppError(409, "INDEX_NOT_READY", "指定模型索引未就绪，请重新处理论文或使用关键词检索")
            return []
        model_id = identity["model_id"]
        self._budget()
        missing = self.connection.execute(
            prefix + " SELECT 1 FROM active a LEFT JOIN process_indexes pi"
            " ON pi.task_id=a.active_process_task_id AND pi.project_id=a.project_id"
            " AND pi.model_id=%s AND pi.dimensions=%s"
            " LEFT JOIN chunk_embeddings ce ON ce.project_id=a.project_id"
            " AND ce.model_id=%s AND ce.chunk_id=a.chunk_id"
            " WHERE a.index_state<>'ready' OR pi.task_id IS NULL OR ce.chunk_id IS NULL LIMIT 1",
            self.values + (model_id, model.dimensions, model_id),
        ).fetchone()
        if missing:
            raise AppError(409, "INDEX_NOT_READY", "指定模型索引未就绪，请重新处理论文或使用关键词检索")
        self._budget()
        # Sorting the computed similarity gives exact ranking, including deterministic ties.
        # No ANN index/approximate filtering is introduced by this extension.
        rows = self.connection.execute(
            prefix + ", scored AS (SELECT a.paper_id,a.page_number,a.chunk_id,"
            " 1 - (ce.embedding <=> %s::vector) AS score"
            " FROM active a JOIN chunk_embeddings ce ON ce.project_id=a.project_id"
            " AND ce.chunk_id=a.chunk_id AND ce.model_id=%s)"
            " SELECT paper_id,page_number,chunk_id,score FROM scored WHERE score>%s"
            " ORDER BY score DESC,paper_id,page_number,chunk_id LIMIT %s",
            self.values + (json.dumps(vector), model_id, threshold, limit),
        ).fetchall()
        return [(self.key(row), row["score"]) for row in rows]

    def resolve(self, keys):
        if not keys:
            return {}
        self._budget()
        rows = self.connection.execute(
            "SELECT * FROM (" + self.active_sql + ") a WHERE chunk_id=ANY(%s::uuid[])",
            self.values + ([key[2] for key in keys],),
        ).fetchall()
        return {self.key(row): (mapping.paper(row), mapping.source(row)) for row in rows}
