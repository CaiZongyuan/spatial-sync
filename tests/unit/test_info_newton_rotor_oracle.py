"""The real I3 receipt must expose transient location loss in any run phase."""

import pytest

from tools.smoke_info_newton import CENTRIFUGE, validate_rotor_samples


def observations():
    return [
        {"centrifuge": {"phase": phase}, "tubeLocations": [f"{CENTRIFUGE}/Rotor/Slot{i}" for i in (1, 3)]}
        for phase in ("accelerating", "running", "decelerating")
    ]


def test_loaded_rotor_retains_both_sites_through_the_complete_run():
    validate_rotor_samples(observations())


def test_rejected_starts_before_loading_are_outside_the_physical_run():
    samples = [{"centrifuge": {"phase": "idle"}, "tubeLocations": ["rack.A1", "rack.A2"]}]
    validate_rotor_samples(samples + observations())


@pytest.mark.parametrize("index", [0, 1, 2])
def test_a_single_loose_observation_fails_even_when_next_observation_recovers(index):
    samples = observations()
    samples.insert(
        index,
        {
            "centrifuge": dict(samples[index]["centrifuge"]),
            "tubeLocations": ["loose", f"{CENTRIFUGE}/Rotor/Slot3"],
        },
    )
    with pytest.raises(AssertionError):
        validate_rotor_samples(samples)


@pytest.mark.parametrize("samples", [[], observations()[:2]])
def test_missing_physics_phases_cannot_establish_stable_locations(samples):
    with pytest.raises(AssertionError):
        validate_rotor_samples(samples)
