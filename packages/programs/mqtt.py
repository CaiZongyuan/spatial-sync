"""Declarative MQTT programs; synchronous callbacks only read/stage mailboxes."""

from typing import Literal

from pydantic import Field, ValidationError, field_validator

from packages.info.program import ProgramConfig, Reject
from packages.protocol.schema import JsonValue, Model, NonEmpty, Positive


class Telemetry(Model):
    topic: NonEmpty
    map: dict[NonEmpty, str]
    timeoutSeconds: Positive = 3

    @field_validator("map")
    @classmethod
    def field_paths(cls, value):
        if not value or any(not path.startswith("$.") or any(not part.isidentifier()
                           for part in path[2:].split(".")) for path in value.values()):
            raise ValueError("Telemetry mapping requires nonempty dotted $.field paths")
        return value


class Command(Model):
    topic: NonEmpty
    ack: NonEmpty
    timeoutSeconds: Positive = 5


class Acknowledgement(Model):
    requestId: NonEmpty
    status: Literal["succeeded", "failed"]
    result: JsonValue = None
    error: NonEmpty = "Device rejected the command"


class MqttProgram:
    tick_priority = 100

    class Config(ProgramConfig):
        telemetry: Telemetry
        commands: dict[NonEmpty, Command] = Field(default_factory=dict)
        statusTopic: NonEmpty | None = None

    def init(self, ctx):
        self.pending = {}
        ctx.driver.mark_offline()
        ctx.driver.incoming.clear()

    def online(self, ctx):
        return ctx.driver.online(ctx.sim_time)

    def accept(self, inv, ctx):
        if not self.online(ctx):
            return Reject(code="device_unavailable", message="Device has no fresh connected telemetry")
        if inv.action not in ctx.config.commands:
            return Reject(code="device_unavailable", message="Device command is not configured")

    def start(self, inv, ctx):
        if not self.online(ctx):
            inv.fail("device_unavailable", "Device disconnected before command dispatch")
            return
        command = ctx.config.commands[inv.action]
        if not ctx.driver.send(command.topic, {"requestId": inv.id, "action": inv.action,
                                               "params": inv.params}):
            inv.fail("device_unavailable", "Device command could not be queued")
            return
        self.pending[inv.id] = inv, command.ack, ctx.sim_time + command.timeoutSeconds

    def _telemetry(self, payload, ctx):
        if not isinstance(payload, dict):
            return
        values = {}
        for prop, path in ctx.config.telemetry.map.items():
            value = payload
            try:
                for part in path[2:].split("."):
                    value = value[part]
                # Validate the whole sample before it can refresh any reading.
                values[prop] = value
            except (KeyError, TypeError):
                return
        try:
            ctx.set(**values)
        except (ValidationError, ValueError, TypeError):
            return
        ctx.driver.mark_online(ctx.sim_time, ctx.config.telemetry.timeoutSeconds)

    def tick(self, ctx, dt):
        for topic, payload, retained in ctx.driver.messages():
            if not ctx.driver.connected:
                continue
            if topic == ctx.config.telemetry.topic:
                if not retained:
                    self._telemetry(payload, ctx)
            elif topic == ctx.config.statusTopic:
                if payload == {"online": False}:
                    ctx.driver.mark_offline()
            elif not retained:
                try:
                    ack = Acknowledgement.model_validate(payload)
                except ValidationError:
                    continue
                pending = self.pending.get(ack.requestId)
                if pending is not None and pending[1] == topic and ctx.sim_time < pending[2]:
                    if self.online(ctx):
                        inv, _, _ = self.pending.pop(ack.requestId)
                        if ack.status == "succeeded":
                            inv.succeed(ack.result)
                        else:
                            inv.fail("device_error", ack.error)
        if not ctx.driver.connected:
            ctx.driver.mark_offline()
        for request_id, (inv, _, deadline) in list(self.pending.items()):
            if not self.online(ctx):
                inv.fail("device_unavailable", "Device disconnected while awaiting confirmation")
            elif ctx.sim_time >= deadline:
                inv.fail("timeout", "Device acknowledgement timed out")
            else:
                continue
            del self.pending[request_id]

    def cancel(self, inv, ctx):
        self.pending.pop(inv.id, None)
        inv.cancelled()
