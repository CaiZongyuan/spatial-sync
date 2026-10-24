"""Actual Synthetic transport fixture with an explicitly controlled comparison producer."""

from copy import deepcopy

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

app = create_app(
    Settings(remote=True, operator_token="owned-rsi-installed", remote_requests_per_minute=10000)
)


def controlled_identity(run):
    # Exercises R3 clients independently of #132's producer; no provenance claim is made here.
    return {
        "conditions": deepcopy(run["key"]),
        "implementation": {
            "environment": "controlled-client-environment",
            "judge": "controlled-client-judge",
        },
    }


app.state.agent_rsi.identities = controlled_identity
