"""Authoring-only alert declarations; the public Catalog keeps its published shape."""

from typing import Literal

from pydantic import Field, StrictStr

from packages.protocol.schema import (
    Condition,
    EntityBinding,
    EntityType,
    Model,
    NonEmpty,
    NonNegative,
)


class AlertRule(Model):
    when: Condition
    forSeconds: NonNegative = 0
    severity: Literal["info", "warning", "critical"]
    message: NonEmpty
    hint: StrictStr | None = None


AlertRules = dict[NonEmpty, AlertRule]


class EntityTypeDeclaration(EntityType):
    alerts: AlertRules = Field(default_factory=dict)


class EntityBindingDeclaration(EntityBinding):
    alerts: AlertRules = Field(default_factory=dict)
