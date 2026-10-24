"""Authoring schemas are HTTP metadata, separate from spatial recording messages."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from packages.training.predictions import prediction


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, serialize_by_alias=True)


Text = Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")]
SceneRef = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")]
TaskRef = Annotated[str, Field(pattern=r"^task/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")]
GoalRef = Annotated[str, Field(pattern=r"^goal/[a-z0-9]+(?:-[a-z0-9]+)*$")]
DemoSetRef = Annotated[str, Field(pattern=r"^demoset/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")]


class Evaluation(Schema):
    scene: SceneRef
    episodes: int = Field(ge=1, le=100000)
    threshold: int | None = Field(default=None, ge=0)
    seeds: list[int] = Field(default_factory=list)
    params: dict[str, list[JsonValue]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def count(self):
        if self.threshold is not None and self.threshold > self.episodes:
            raise ValueError("threshold cannot exceed episodes")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("evaluation seeds must be distinct")
        return self


class LoopBudget(Schema):
    gpuHours: float = Field(ge=0)
    episodes: int = Field(ge=1)
    days: float = Field(gt=0)
    llmUsd: float | None = Field(default=None, ge=0)


class Control(Schema):
    hz: float = Field(gt=0)
    action: Text


class IsaacLab(Schema):
    task_id: Text
    presets: list[Text] = Field(min_length=1)
    sceneInput: Literal["usd", "scene-package"] = "usd"


class Episode(Schema):
    params: dict[str, JsonValue] = Field(default_factory=dict)


class DemonstrationUse(Schema):
    set: DemoSetRef
    purpose: Literal["reset", "imitation"]


class TaskMetadata(Schema):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: Text
    scene: SceneRef
    robot: str = Field(pattern=r"^/World/Lab/.+")
    implements: str = Field(pattern=r"^[a-z0-9-]+/v[1-9][0-9]*\.[A-Za-z][A-Za-z0-9]*$")
    episode: Episode
    success: dict[str, JsonValue]
    control: Control
    observation: Text
    isaaclab: IsaacLab
    evaluation: Evaluation
    loopBudget: LoopBudget
    demonstrations: list[DemonstrationUse] = Field(default_factory=list, max_length=32)

    @field_validator("demonstrations")
    @classmethod
    def distinct_demonstrations(cls, value):
        if len({(item.set, item.purpose) for item in value}) != len(value):
            raise ValueError("Demonstration Set usages must be distinct")
        return value

    @field_validator("success")
    @classmethod
    def condition_structure(cls, value):
        pending = [value]
        while pending:
            condition = pending.pop()
            if not isinstance(condition, dict):
                raise ValueError("Success conditions must be mappings")
            for key in ("entity", "property"):
                if key in condition and (not isinstance(condition[key], str) or not condition[key]):
                    raise ValueError(f"Condition {key} must be a nonempty string")
            if "all" in condition:
                if not isinstance(condition["all"], list):
                    raise ValueError("All requires an array of conditions")
                pending.extend(condition["all"])
        return value

    @model_validator(mode="after")
    def evaluation_scene(self):
        if self.evaluation.scene != self.scene:
            raise ValueError("Task evaluation must pin the Task scene")
        if not self.evaluation.seeds or not self.evaluation.params or any(
            not values for values in self.evaluation.params.values()
        ):
            raise ValueError("Task evaluation must declare fixed seeds and nonempty parameter coverage")
        return self


class Prediction(Schema):
    metric: Text
    to: float | str
    # `from` is an optional numeric baseline, not a requirement for a new metric.
    baseline: float | None = Field(default=None, alias="from")

    @field_validator("to")
    @classmethod
    def measurable(cls, value):
        try:
            prediction(value)
        except ValueError:
            raise ValueError("predict.to requires a finite number or numeric comparison") from None
        return value


class Hypothesis(Schema):
    id: Text
    cause: Text
    change: Text
    predict: Prediction


class Jobs(Schema):
    seeds: list[int] = Field(default_factory=list)

    @field_validator("seeds")
    @classmethod
    def distinct(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("job seeds must be distinct")
        return value


class PlanBudget(Schema):
    gpuHours: float = Field(ge=0)
    episodes: int | None = Field(default=None, ge=1)


class TrainingPlan(Schema):
    id: str = Field(pattern=r"^plan-[A-Za-z0-9-]+$")
    kind: Literal["training"] = "training"
    title: Text
    goal: Text
    goalId: GoalRef | None = None
    task: TaskRef
    previous: str | None = None
    algorithm: dict[str, JsonValue] = Field(min_length=1)
    model: dict[str, JsonValue] = Field(min_length=1)
    hyperparameters: dict[str, JsonValue] = Field(min_length=1)
    jobs: Jobs
    budget: PlanBudget
    evaluation: Evaluation
    rationale: Text
    hypotheses: list[Hypothesis] = Field(min_length=1)
    notebookRead: list[Text]
    interview: str = Field(min_length=1, max_length=128000, pattern=r"\S")
    revision: int = Field(default=1, ge=1)
    status: Literal["draft", "submitted", "approved", "rejected", "completed", "abandoned"] = "draft"

    @field_validator("hypotheses")
    @classmethod
    def unique_hypotheses(cls, value):
        if len({item.id for item in value}) != len(value):
            raise ValueError("hypothesis IDs must be distinct")
        return value
