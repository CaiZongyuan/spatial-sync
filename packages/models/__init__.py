"""Persistent LLM configuration and human-maintained price tables, without credentials."""

import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResourceError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.status = code, status


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")


Provider = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
ModelId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}$")]
Thinking = Literal["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"]
Rate = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


class ModelConfig(Document):
    provider: Provider
    modelId: ModelId
    defaultThinking: Thinking = "default"
    credentialName: str | None = None

    @property
    def ref(self):
        return f"{self.provider}/{self.modelId}"


class ModelTable(Document):
    version: Literal[1] = 1
    models: list[ModelConfig] = Field(default_factory=list)
    defaultRef: str | None = None

    @model_validator(mode="after")
    def consistent(self):
        refs = [model.ref for model in self.models]
        if len(refs) != len(set(refs)) or self.defaultRef is not None and self.defaultRef not in refs:
            raise ValueError("Model references must be unique and the default must exist")
        return self


class ModelPrice(Document):
    provider: Provider
    modelId: ModelId
    inputUsdPerMillion: Rate | None = None
    outputUsdPerMillion: Rate | None = None
    cacheReadUsdPerMillion: Rate | None = None
    cacheWriteUsdPerMillion: Rate | None = None


class GpuPrice(Document):
    nodeId: Annotated[str, Field(min_length=1, max_length=128)]
    usdPerHour: Rate | None = None


class PricesInput(Document):
    revision: Annotated[int, Field(strict=True, ge=0)] = 0
    models: list[ModelPrice] = Field(default_factory=list)
    gpus: list[GpuPrice] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self):
        models = [(item.provider, item.modelId) for item in self.models]
        nodes = [item.nodeId for item in self.gpus]
        if len(set(models)) != len(models) or len(set(nodes)) != len(nodes):
            raise ValueError("Price references must be unique")
        return self


class PriceAudit(Document):
    revision: int
    at: str
    actor: dict[str, str]


class Prices(PricesInput):
    version: Literal[1] = 1
    audit: list[PriceAudit] = Field(default_factory=list)


@contextmanager
def _locked(root):
    root.mkdir(parents=True, exist_ok=True)
    if (root / "models.lock").is_symlink():
        raise ResourceError("resource_unavailable", "Resource lock cannot be a symbolic link", 503)
    with (root / "models.lock").open("a+b") as lock:
        if not lock.tell():
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read(path, schema):
    if path.is_symlink():
        raise ResourceError("resource_unavailable", "Resource configuration cannot be a symbolic link", 503)
    if not path.exists():
        return schema()
    try:
        return schema.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, yaml.YAMLError):
        raise ResourceError(
            "resource_unavailable", "Resource configuration is invalid or unreadable", 503
        ) from None


def _write(path, document):
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".models-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            yaml.safe_dump(document.model_dump(mode="json"), output, sort_keys=False, allow_unicode=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError:
        raise ResourceError("resource_unavailable", "Cannot persist resource configuration", 503) from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


class ModelResources:
    def __init__(self, root):
        self.root = Path(root)

    def models(self, query=None):
        with _locked(self.root):
            table = _read(self.root / "models.yaml", ModelTable)
        result = []
        for model in table.models:
            if query and query.casefold() not in model.ref.casefold():
                continue
            result.append(
                model.model_dump()
                | {
                    "ref": model.ref,
                    "isDefault": model.ref == table.defaultRef,
                    "health": "unknown",
                    "healthReason": "Adapter request metrics are not connected",
                    "latencyP50Ms": None,
                    "users": None,
                }
            )
        return {"models": result, "defaultRef": table.defaultRef}

    def add(self, model):
        with _locked(self.root):
            path = self.root / "models.yaml"
            table = _read(path, ModelTable)
            if any(item.ref == model.ref for item in table.models):
                raise ResourceError("model_exists", "The model is already registered", 409)
            table.models.append(model)
            _write(path, table)
        return next(item for item in self.models()["models"] if item["ref"] == model.ref)

    def set_default(self, ref):
        with _locked(self.root):
            path = self.root / "models.yaml"
            table = _read(path, ModelTable)
            if not any(item.ref == ref for item in table.models):
                raise ResourceError("unknown_model", "Register the model before selecting it as default", 404)
            table.defaultRef = ref
            _write(path, table)
        return {"defaultRef": ref}

    def prices(self):
        with _locked(self.root):
            return _read(self.root / "prices.yaml", Prices).model_dump(mode="json")

    def put_prices(self, incoming, actor):
        with _locked(self.root):
            path = self.root / "prices.yaml"
            previous = _read(path, Prices)
            if incoming.revision != previous.revision:
                raise ResourceError("prices_changed", "Prices changed; reload before saving again", 409)
            revision = previous.revision + 1
            audit = PriceAudit(revision=revision, at=datetime.now(UTC).isoformat(), actor=actor)
            document = Prices(
                **(incoming.model_dump() | {"revision": revision}), audit=[*previous.audit, audit]
            )
            _write(path, document)
        return document.model_dump(mode="json")
