"""Authoring schemas are HTTP metadata, separate from spatial recording messages."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from packages.programs.metadata import ProgramIntent
from packages.training.declarative import Completion, LayerA, ReferenceMotion, ReferencePin
from packages.training.predictions import prediction
from packages.training.reference_initialization import MAX_COUNT, MAX_SHIFT, MAX_STATISTIC, MAX_WIDTH, SCHEMA


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


class TrialPerturbation(Schema):
    jointPositionRadians: float = Field(default=0.025, ge=0.01, le=0.1)
    jointPositionMeters: float = Field(default=0.005, ge=0.001, le=0.05)


class PlanEvaluation(Evaluation):
    episodes: int = Field(default=30, ge=1, le=100000)
    perturbation: TrialPerturbation | None = Field(default=None, exclude_if=lambda value: value is None)


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


class MjLab(Schema):
    task_id: Text
    sceneInput: Literal["usd", "scene-package"] = "usd"
    physics_dt: float | None = Field(default=None, gt=0, exclude_if=lambda value: value is None)
    decimation: int | None = Field(default=None, ge=1, exclude_if=lambda value: value is None)


NumericBound = Annotated[list[float | int], Field(min_length=2, max_length=2)]


class Episode(Schema):
    params: dict[str, JsonValue] = Field(default_factory=dict)
    paramBounds: dict[str, NumericBound] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    commandEnvelope: list[NumericBound] | None = Field(
        default=None, min_length=1, max_length=64, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def ordered_bounds(self):
        for bounds in [*(self.paramBounds or {}).values(), *(self.commandEnvelope or [])]:
            if any(isinstance(value, bool) for value in bounds) or float(bounds[0]) > float(bounds[1]):
                raise ValueError("Bounds require finite ordered [lower, upper] numbers")
        return self


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
    isaaclab: IsaacLab | None = Field(default=None, exclude_if=lambda value: value is None)
    mjlab: MjLab | None = Field(default=None, exclude_if=lambda value: value is None)
    declarative: LayerA | None = Field(default=None, exclude_if=lambda value: value is None)
    completion: Completion | None = Field(default=None, exclude_if=lambda value: value is None)
    referenceMotion: ReferenceMotion | None = Field(default=None, exclude_if=lambda value: value is None)
    referenceMotionRef: ReferencePin | None = Field(default=None, exclude_if=lambda value: value is None)
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
        if (self.isaaclab is None) == (self.mjlab is None):
            raise ValueError("Task must configure exactly one training backend: isaaclab or mjlab")
        if (
            self.mjlab is not None
            and self.mjlab.physics_dt is not None
            and self.mjlab.decimation is not None
            and abs(1.0 / (self.mjlab.physics_dt * self.mjlab.decimation) - self.control.hz) > 1e-4
        ):
            raise ValueError("mjlab physics_dt * decimation must match control.hz")
        if self.declarative is not None:
            if (self.mjlab is None or self.mjlab.sceneInput != "usd"
                    or self.mjlab.physics_dt is None or self.mjlab.decimation is None
                    or not self.declarative.baseBody.startswith(self.robot + "/")):
                raise ValueError("Declarative Task requires managed USD mjlab, explicit clocks and base body")
            kinds = {reward.kind for reward in self.declarative.rewards}
            if ("joint_tracking" in kinds and self.referenceMotion is None
                    or "velocity_tracking" in kinds and not self.declarative.command
                    or self.demonstrations):
                raise ValueError("Layer A tracking requires matching immutable motion or command inputs")
            if kinds & {"root_pose_tracking", "root_velocity_tracking", "body_local_tracking"}:
                if self.referenceMotionRef is None or self.referenceMotion is None:
                    raise ValueError("Root and body tracking require a published Reference Motion")
                if "body_local_tracking" in kinds and not self.referenceMotion.frames[0].bodyLocal:
                    raise ValueError("Body tracking requires Reference Motion local body coordinates")
        if self.referenceMotionRef is not None:
            if self.declarative is None or self.referenceMotion is None:
                raise ValueError("Reference Motion pins require declarative Task frames")
            completion = self.completion or Completion(mode="phase_clip")
            if completion.mode != "phase_clip":
                raise ValueError("A pinned Reference Motion requires phase_clip completion")
            duration = self.referenceMotion.frames[-1].time
            if self.referenceMotion.loop:
                duration *= completion.cycles
            duration += completion.dwellSeconds
            if self.declarative.episodeSeconds < duration:
                raise ValueError("Episode must cover the Reference Motion and completion dwell")
        if self.completion is not None:
            if self.completion.mode == "phase_clip" and self.referenceMotion is None:
                raise ValueError("phase_clip requires a pinned reference motion")
            if self.completion.mode in ("command_track", "waypoint_nav") and not self.episode.commandEnvelope:
                raise ValueError("Tracking and navigation require a command envelope")
        if self.evaluation.scene != self.scene:
            raise ValueError("Task evaluation must pin the Task scene")
        has_episode_params = bool(self.episode.params) or bool(self.episode.paramBounds)
        if (
            not self.evaluation.seeds
            or (has_episode_params and not self.evaluation.params)
            or any(not values for values in self.evaluation.params.values())
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


InitHash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class InitializationStatistics(Schema):
    mean: list[Annotated[float, Field(ge=-MAX_STATISTIC, le=MAX_STATISTIC)]] = Field(
        min_length=1, max_length=MAX_WIDTH,
    )
    variance: list[Annotated[float, Field(ge=0, le=MAX_STATISTIC)]] = Field(
        min_length=1, max_length=MAX_WIDTH,
    )
    count: int = Field(ge=1, le=MAX_COUNT)


class ReferenceActorInitialization(Schema):
    schema_version: Literal[SCHEMA] = Field(alias="schema")
    taskContentHash: InitHash
    referenceMotionSha256: InitHash
    observationSha256: InitHash
    sha256: InitHash
    initialStd: float = Field(ge=1e-4, le=2)
    shift: float = Field(gt=0, le=MAX_SHIFT)
    fitActionAbsMax: float | None = Field(
        default=None, ge=0, le=MAX_SHIFT, exclude_if=lambda value: value is None,
    )
    normalization: InitializationStatistics


class TrainingPlan(Schema):
    id: str = Field(pattern=r"^plan-[A-Za-z0-9-]+$")
    kind: Literal["training"] = "training"
    title: Text
    goal: Text
    goalId: GoalRef | None = None
    task: TaskRef
    previous: str | None = None
    program: ProgramIntent | None = None
    algorithm: dict[str, JsonValue] = Field(default_factory=dict)
    model: dict[str, JsonValue] = Field(default_factory=dict)
    hyperparameters: dict[str, JsonValue] = Field(default_factory=dict)
    initialization: ReferenceActorInitialization | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    jobs: Jobs
    budget: PlanBudget
    evaluation: PlanEvaluation
    rationale: Text
    hypotheses: list[Hypothesis] = Field(min_length=1)
    notebookRead: list[Text]
    interview: str = Field(min_length=1, max_length=128000, pattern=r"\S")
    revision: int = Field(default=1, ge=1)
    status: Literal["draft", "submitted", "approved", "rejected", "completed", "abandoned"] = "draft"

    @model_validator(mode="after")
    def work_kind(self):
        training = (self.algorithm, self.model, self.hyperparameters)
        if self.program is None:
            if not all(training):
                raise ValueError("A training round requires algorithm, model and hyperparameters")
        elif (any(training) or self.initialization is not None or self.jobs.seeds
              or self.budget.gpuHours != 0 or self.budget.episodes is None):
            raise ValueError(
                "A script round has no training metadata/Jobs/GPU budget and requires episode budget"
            )
        return self

    @field_validator("hypotheses")
    @classmethod
    def unique_hypotheses(cls, value):
        if len({item.id for item in value}) != len(value):
            raise ValueError("hypothesis IDs must be distinct")
        return value
