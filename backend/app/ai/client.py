import json
import math

import httpx
from pydantic import ValidationError

from app.ai.settings import AISettings
from app.errors import AppError


def invalid_response():
    return AppError(502, "AI_INVALID_RESPONSE", "模型响应格式无效，请稍后重试")


class ModelClient:
    """OpenAI-compatible embeddings and strict JSON chat; no retries or shared sockets.

    Each call closes its client on success, failure and cancellation. Redirects are
    disabled, so a provider cannot redirect the Authorization header elsewhere.
    """

    def __init__(self, settings: AISettings, *, transport=None):
        self.settings = settings
        self.transport = transport

    async def _post(self, endpoint, payload):
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self.settings.base_url.rstrip("/") + endpoint,
                    headers={"Authorization": f"Bearer {self.settings.api_key}"},
                    json=payload,
                ) as response:
                    if response.status_code in {408, 504}:
                        raise AppError(504, "AI_TIMEOUT", "模型请求超时，请稍后重试")
                    if response.status_code == 429 or response.status_code >= 500:
                        raise AppError(503, "AI_UNAVAILABLE", "模型服务暂不可用，请稍后重试")
                    if response.status_code != 200:
                        raise AppError(502, "AI_PROVIDER_ERROR", "模型服务拒绝请求，请检查服务配置")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 8 * 1024 * 1024:
                            raise invalid_response()
                    return json.loads(body)
        except httpx.TimeoutException:
            raise AppError(504, "AI_TIMEOUT", "模型请求超时，请稍后重试") from None
        except httpx.HTTPError:
            raise AppError(503, "AI_UNAVAILABLE", "模型服务暂不可用，请稍后重试") from None
        except (ValueError, UnicodeError):
            raise invalid_response() from None
        except AppError:
            raise
        except Exception:
            raise AppError(503, "AI_UNAVAILABLE", "模型服务暂不可用，请稍后重试") from None

    async def embed(self, texts):
        vectors = []
        # Bound individual requests, preserving input order even if the provider reorders rows.
        for start in range(0, len(texts), 32):
            batch = texts[start : start + 32]
            response = await self._post(
                "/embeddings",
                {
                    "model": self.settings.embedding_model,
                    "input": batch,
                    "encoding_format": "float",
                },
            )
            try:
                rows = response["data"]
                if (
                    len(rows) != len(batch)
                    or any(type(r["index"]) is not int for r in rows)
                    or {r["index"] for r in rows} != set(range(len(batch)))
                ):
                    raise ValueError("invalid indexes")
                for row in sorted(rows, key=lambda r: r["index"]):
                    vector = row["embedding"]
                    if len(vector) != self.settings.embedding_dimensions or any(
                        type(v) not in {int, float} or not math.isfinite(v) for v in vector
                    ):
                        raise ValueError("invalid vector")
                    norm = math.hypot(*vector)
                    if not norm or not math.isfinite(norm):
                        raise ValueError("invalid norm")
                    vectors.append([v / norm for v in vector])
            except (KeyError, TypeError, ValueError, OverflowError):
                raise invalid_response() from None
        return vectors

    async def generate(self, shape, *, instruction, payload):
        schema = shape.model_json_schema()
        if self.settings.response_format == "json_object":
            instruction += "\nReturn a JSON object matching this JSON Schema exactly: " + json.dumps(
                schema, ensure_ascii=False
            )
            response_format = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {"name": shape.__name__, "strict": True, "schema": schema},
            }
        response = await self._post(
            "/chat/completions",
            {
                "model": self.settings.chat_model,
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "response_format": response_format,
                self.settings.token_limit_parameter: 4000,
            },
        )
        try:
            choice = response["choices"][0]
            message = choice["message"]
            if choice["finish_reason"] != "stop" or message.get("refusal"):
                raise ValueError("refused or incomplete")
            return shape.model_validate_json(message["content"])
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
            raise invalid_response() from None
