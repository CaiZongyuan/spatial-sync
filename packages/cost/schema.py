"""Reserved measured LLM record; #44 will supply actual adapter/client receipts."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Count = Annotated[int, Field(strict=True, ge=0)]


class LlmUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestId: str = Field(min_length=1, max_length=4000)
    receivedAt: float = Field(allow_inf_nan=False)
    origin: Literal["adapter", "self-reported"]
    agent: str
    harnessVersion: int | None
    provider: str
    modelId: str
    inputTokens: Count
    outputTokens: Count
    cacheReadTokens: Count
    succeeded: bool
    planId: str | None
    planRevision: int | None
    evaluationId: str | None
    traceId: str | None
