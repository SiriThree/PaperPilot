"""One chat-only diagnostic request. Never prints API keys or provider response bodies."""

import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app.ai.client import ModelClient  # noqa: E402
from app.ai.settings import ChatSettings  # noqa: E402
from app.ai.sql_generation import ModelSQLGenerator  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.security.sql_policy import SQL_BY_QUESTION, SQLPolicyRejected, prepare_query  # noqa: E402
from uuid import uuid4  # noqa: E402


class StatusTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport()
        self.status = None

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        self.status = response.status_code
        return response

    async def aclose(self):
        await self.inner.aclose()


async def probe(settings):
    transport = StatusTransport()
    report = {
        "base_url": settings.base_url,
        "model": settings.chat_model,
        "response_format": settings.response_format,
        "token_limit_parameter": settings.token_limit_parameter,
        "http_status": None,
        "database_executed": False,
    }
    try:
        generator = ModelSQLGenerator(ModelClient(settings, transport=transport), SQL_BY_QUESTION)
        sql = await generator(None, "按年份统计论文数量")
        if sql is None:
            report.update(ok=False, code="UNEXPECTED_UNSUPPORTED_QUERY")
        else:
            prepare_query(sql, uuid4())
            report.update(ok=True, code="MODEL_AND_SQL_POLICY_OK")
    except AppError as error:
        report.update(ok=False, code=error.code)
    except SQLPolicyRejected:
        report.update(ok=False, code="SQL_POLICY_REJECTED")
    except Exception:
        report.update(ok=False, code="MODEL_CHECK_FAILED")
    finally:
        await transport.aclose()
    report["http_status"] = transport.status
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response-format", choices=("json_schema", "json_object"))
    args = parser.parse_args()
    prefix = (
        "PAPERPILOT_SQL_MODEL_"
        if any(k.startswith("PAPERPILOT_SQL_MODEL_") for k in os.environ)
        else "PAPERPILOT_AI_"
    )
    try:
        settings = ChatSettings.from_env(prefix)
    except Exception:
        print(json.dumps({"ok": False, "code": "MODEL_CONFIGURATION_INVALID", "config_prefix": prefix}))
        return 1
    if args.response_format:
        settings = replace(settings, response_format=args.response_format)
    report = asyncio.run(probe(settings))
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
