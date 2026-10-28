"""Real C algorithms + real PDF parser, isolated DataPort and HTTP model doubles."""

from contextlib import contextmanager

import httpx

from app.adapters.mock_data import MockData, seed_id
from app.adapters.ports import Context, FileReference
from app.ai.adapter import RealAI
from app.ai.files import LocalPDFReader
from app.schemas import SearchRequest

from .harness import AIHarness
from .support import configuration, model_client, pdf_bytes


@contextmanager
def build_harness(*, tmp_path):
    data = MockData()
    ctx = Context(seed_id("alice"), seed_id("project-a"), seed_id("c-request"))
    other = Context(seed_id("eve"), seed_id("project-b"), seed_id("c-other-request"))
    file = FileReference(f"{seed_id('paper-a')}.pdf", seed_id("paper-a"), ctx.project_id)
    (tmp_path / file.storage_key).write_bytes(
        pdf_bytes(
            [
                "Retrieval augmented generation checks evidence provenance.",
                "Limitations: this is an original test fixture, not a published study.",
            ]
        )
    )
    settings = configuration()
    reader = LocalPDFReader(tmp_path)

    def failing(request):
        raise httpx.ConnectError("PRIVATE_PROVIDER_BODY fixture-secret-not-a-key", request=request)

    yield AIHarness(
        RealAI(data, settings, reader=reader, client=model_client(settings)),
        RealAI(data, settings, reader=reader, client=model_client(settings, failing)),
        data,
        ctx,
        other,
        seed_id("c-task"),
        file,
        SearchRequest(query="检索增强生成"),
        data.sources(ctx, file.paper_id)[0],
        "Please summarize the evidence",
        False,
    )
