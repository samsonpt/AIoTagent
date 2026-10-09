from __future__ import annotations

import json
import os
from typing import Protocol

from openai import OpenAI
from pydantic import BaseModel, ValidationError


class LlmClient(Protocol):
    def complete(self, messages: list[dict], *, response_model: type[BaseModel]) -> BaseModel: ...


_NOOP_SUPERVISOR = {"route": "end", "reason": "noop"}


def default_fake_llm(*, supervisor_cycles: int = 512) -> "FakeLLM":
    return FakeLLM(
        {"SupervisorDecision": [_NOOP_SUPERVISOR.copy() for _ in range(supervisor_cycles)]}
    )


class FakeLLM:
    def __init__(self, scripts: dict[str, list[dict] | dict]) -> None:
        self._scripts: dict[str, list[dict]] = {}
        self.call_count = 0
        for key, value in scripts.items():
            if isinstance(value, list):
                self._scripts[key] = list(value)
            else:
                self._scripts[key] = [value]

    def complete(self, messages: list[dict], *, response_model: type[BaseModel]) -> BaseModel:
        name = response_model.__name__
        if name not in self._scripts or not self._scripts[name]:
            raise KeyError(name)
        data = self._scripts[name].pop(0)
        self.call_count += 1
        return response_model.model_validate(data)


class DeepSeekLLM:
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-chat"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY")
        self._base_url = (
            base_url
            if base_url is not None
            else os.environ.get("DEEPSEEK_BASE_URL", self.DEFAULT_BASE_URL)
        )
        self._model = (
            model if model is not None else os.environ.get("DEEPSEEK_MODEL", self.DEFAULT_MODEL)
        )
        self._client: OpenAI | None = None
        if self._api_key:
            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)

    def complete(self, messages: list[dict], *, response_model: type[BaseModel]) -> BaseModel:
        if not self._api_key or self._client is None:
            raise ValueError("DEEPSEEK_API_KEY is required")

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=_with_json_instruction(messages, response_model),
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if content is None:
                raise ValueError("empty response from DeepSeek API")
            data = json.loads(content)
            return response_model.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"invalid LLM response for {response_model.__name__}: {exc}") from exc
        except ValueError:
            raise
        except Exception as exc:
            detail = str(exc)
            if self._api_key:
                detail = detail.replace(self._api_key, "***")
            raise ValueError(f"DeepSeek API call failed: {detail}") from exc


def _with_json_instruction(messages: list[dict], response_model: type[BaseModel]) -> list[dict]:
    schema = json.dumps(response_model.model_json_schema(), sort_keys=True, ensure_ascii=False)
    instruction = {
        "role": "system",
        "content": (
            f"Respond with a single JSON object matching this schema for {response_model.__name__}: "
            f"{schema}"
        ),
    }
    return [instruction, *messages]
