"""Gateway-owned alert episodes driven by ordered simulation observations and clocks."""

from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4

from packages.protocol.schema import EntityAlert, EntityEvent

from .conditions import condition_state


class AlertUnavailable(ValueError):
    pass


@dataclass
class _Episode:
    latest: EntityAlert
    acknowledgement: dict | None = None
    closed_context: dict | None = None

    @property
    def active(self):
        return self.closed_context is None and self.latest.state != "cleared"

    def document(self):
        return self.latest.model_dump(mode="json", exclude_none=True) | {
            "active": self.active,
            "acknowledgement": deepcopy(self.acknowledgement),
            "closedContext": deepcopy(self.closed_context),
        }


class AlertEngine:
    def __init__(self, catalog, epoch=0):
        self.revision, self.epoch = catalog.sceneRevision, epoch
        self.rules = catalog.alerts()
        self.sim_time, self.sample_time_ms = 0.0, 0.0
        self._candidates, self._active, self._episodes = {}, {}, {}

    def documents(self):
        return [episode.document() for episode in reversed(self._episodes.values())]

    def get(self, alert_id):
        episode = self._episodes.get(alert_id)
        return episode.document() if episode else None

    def configure(self, catalog):
        self.close_context("scene_reloaded")
        self.revision, self.epoch, self.rules = catalog.sceneRevision, 0, catalog.alerts()
        self.sim_time, self.sample_time_ms = 0.0, 0.0

    def close_context(self, reason):
        """End the context without claiming that an unavailable condition recovered."""
        for alert_id in self._active.values():
            self._episodes[alert_id].closed_context = {
                "reason": reason,
                "epoch": self.epoch,
                "simTime": self.sim_time,
            }
        self._active.clear()
        self._candidates.clear()

    def advance(self, epoch, sim_time, sample_time_ms, values, metadata):
        if epoch < self.epoch:
            return []
        if epoch > self.epoch:
            self.close_context("session_reset")
            self.epoch, self.sim_time = epoch, 0.0
        if sim_time < self.sim_time:
            return []
        self.sim_time, self.sample_time_ms = sim_time, sample_time_ms
        messages = []
        for identity, (rule, conditions) in self.rules.items():
            key, name = identity
            state = condition_state(conditions, key, values, metadata)
            alert_id = self._active.get(identity)
            if state is not True:
                self._candidates.pop(identity, None)
                if state is False and alert_id:
                    episode = self._episodes[alert_id]
                    first = conditions[0]
                    value = values.get(first.get("entity", key), {}).get(first["property"])
                    episode.latest = episode.latest.model_copy(
                        update={
                            "state": "cleared",
                            "simTime": sim_time,
                            "sampleTimeMs": sample_time_ms,
                            "value": deepcopy(value),
                            "actor": None,
                        }
                    )
                    del self._active[identity]
                    messages.append(episode.latest)
                continue
            if alert_id:
                continue
            since = self._candidates.setdefault(identity, sim_time)
            if sim_time - since + 1e-10 < rule["forSeconds"]:
                continue
            first = conditions[0]
            value = values.get(first.get("entity", key), {}).get(first["property"])
            alert = EntityAlert(
                sceneRevision=self.revision,
                epoch=epoch,
                simTime=sim_time,
                sampleTimeMs=sample_time_ms,
                entityKey=key,
                alertId="alert-" + uuid4().hex,
                rule=name,
                severity=rule["severity"],
                state="raised",
                message=rule["message"],
                hint=rule.get("hint"),
                value=deepcopy(value),
                since=since,
            )
            self._episodes[alert.alertId] = _Episode(alert)
            self._active[identity] = alert.alertId
            messages.append(alert)
        return messages

    def acknowledge(self, alert_id, actor, note, sample_time_ms):
        episode = self._episodes[alert_id]
        if not episode.active:
            raise AlertUnavailable("Alert is cleared or its session context has ended")
        if episode.acknowledgement is not None:
            return episode.document(), []
        latest = episode.latest.model_copy(
            update={
                "state": "acknowledged",
                "simTime": self.sim_time,
                "sampleTimeMs": sample_time_ms,
                "actor": actor,
            }
        )
        episode.latest = latest
        episode.acknowledgement = {
            "actor": actor.model_dump(mode="json"),
            "note": note,
            "simTime": self.sim_time,
            "sampleTimeMs": sample_time_ms,
        }
        audit = EntityEvent(
            sceneRevision=self.revision,
            epoch=self.epoch,
            simTime=self.sim_time,
            sampleTimeMs=sample_time_ms,
            entityKey=latest.entityKey,
            event="alert.acknowledged",
            data={"alertId": alert_id, "actor": actor.model_dump(mode="json"), "note": note},
        )
        return episode.document(), [latest, audit]
