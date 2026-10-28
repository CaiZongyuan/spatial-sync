"""Owned CLI/browser fixture: controlled scores/qualification, actual publication, zero Sources."""

import os

import anyio
from pydantic import BaseModel, ConfigDict

from apps.api.app.library_validation import program_schemas
from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from tests.fixtures.controller_promotion_evidence import (
    complete_controlled_tests,
    controlled_qualification,
    seed_controlled_train,
)
from tests.gateway.test_training_jobs import TOKEN
from tests.unit.test_controller_publication import ControlledPublicationValidator

os.environ.pop("SPATIAL_CATALOG_METADATA_ROOT", None)
settings = Settings(node_token=TOKEN)
starts = []
publication_fault = {"active": False}


def no_source(*args, **kwargs):
    starts.append((args, kwargs))
    raise AssertionError("Controlled promotion fixture must start no Source")


app = create_app(settings, source_factory=no_source,
                 controller_promotion_qualification=controlled_qualification,
                 controller_publication_validator=ControlledPublicationValidator(
                     settings.state_root / "controlled-validation", program_schemas
                 ))
app.state.manager.evaluations._busy = lambda: True


def fail_after_scene(effect, _):
    if publication_fault["active"] and effect == "scene_published":
        raise OSError("Controlled interruption after Scene publication")


app.state.controller_promotions.publication.after_effect = fail_after_scene


class Seed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    policy: str


class Fault(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    active: bool


@app.post("/fixture/controller-promotions/publication-fault")
async def fault(body: Fault):
    publication_fault["active"] = body.active
    return {"evidenceMode": "controlled", "active": body.active}


@app.post("/fixture/controller-promotions/train")
async def train(body: Seed):
    manager = app.state.manager
    policy, _ = manager.policies.resolve(body.policy)
    scene = await anyio.to_thread.run_sync(
        lambda: seed_controlled_train(
            manager, policy, manager.jobs.get(policy["jobId"]), successes=1, episodes=1
        )
    )
    return {"evidenceMode": "controlled", "scene": scene.id, "sourceStarts": len(starts)}


@app.post("/fixture/controller-promotions/{identifier}/complete-tests")
async def complete(identifier: str):
    service = app.state.controller_promotions
    await anyio.to_thread.run_sync(complete_controlled_tests, service, service.get(identifier))
    return service.public(service.get(identifier)) | {
        "sourceStarts": len(starts), "evidenceMode": "controlled"
    }
