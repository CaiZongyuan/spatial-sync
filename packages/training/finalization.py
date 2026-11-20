"""Verify that a leased Checkpoint can reach native observation capture."""

from apps.node import environment_observer, native_observation_capture
from apps.node.observation_capture import code_hash
from packages.info.plans import PlanError


def finalization_profile(manager, job, runtime, *, task=None):
    from packages.policies.qualification import declarative_profile

    if task is None:
        task = manager.tasks.show(job["task"])
        manager.tasks.require_execution(task)
    num_envs = job["plan"]["hyperparameters"].get("num_envs")
    if not runtime or runtime.get("entry") != "mjlab:managed" or type(num_envs) is not int or num_envs < 1:
        raise PlanError("observation_source_unverified", "Finalization needs reviewed mjlab", status=409)

    spec = manager.tasks.observation(job["task"])
    if task["task"].get("declarative") is not None:
        profile = declarative_profile(task, spec, job, runtime).model_dump(mode="json")
    else:
        profile = manager.policy_evidence.native_profiles.for_task(
            job["taskContentHash"],
            job["plan"]["algorithm"]["version"],
        )
    if (
        not profile
        or profile["evidenceMode"] != "native"
        or profile["taskContentHash"] != task["contentHash"]
        or profile["versions"] != runtime.get("trainerVersions")
        or (profile.get("compilerHash") and profile["compilerHash"] != runtime.get("compilerHash"))
        or profile["inputWidth"] != spec.size
        or profile["actionWidth"] != len(spec.action.jointKeys)
        or profile["environments"] != list(range(min(4, num_envs)))
        or profile["collectorSha256"] != code_hash(native_observation_capture.__file__)
        or profile["observerSha256"] != code_hash(environment_observer.__file__)
    ):
        raise PlanError(
            "observation_source_unverified",
            "Checkpoint finalization requires an eligible reviewed native observation profile",
            status=409,
        )
    return profile
