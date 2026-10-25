"""Owned CLI/browser fixture: controlled scores/qualification, actual publication, zero Sources."""

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

settings = Settings(node_token=TOKEN)
starts = []


def no_source(*args, **kwargs):
    starts.append((args, kwargs))
    raise AssertionError("Controlled promotion fixture must start no Source")


app = create_app(settings, source_factory=no_source,
                 controller_promotion_qualification=controlled_qualification,
                 controller_publication_validator=ControlledPublicationValidator(
                     settings.state_root / "controlled-validation", program_schemas
                 ))
app.state.manager.evaluations._busy = lambda: True


class Seed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    policy: str


@app.post("/fixture/controller-promotions/train")
async def train(body: Seed):
    manager = app.state.manager
    policy, _ = manager.policies.resolve(body.policy)
    scene = await anyio.to_thread.run_sync(
        seed_controlled_train, manager, policy, manager.jobs.get(policy["jobId"])
    )
    return {"evidenceMode": "controlled", "scene": scene.id, "sourceStarts": len(starts)}


@app.post("/fixture/controller-promotions/{identifier}/complete-tests")
async def complete(identifier: str):
    service = app.state.controller_promotions
    await anyio.to_thread.run_sync(complete_controlled_tests, service, service.get(identifier))
    return service.public(service.get(identifier)) | {
        "sourceStarts": len(starts), "evidenceMode": "controlled"
    }
