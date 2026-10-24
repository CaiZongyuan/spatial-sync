"""Actual Synthetic transport fixture using the production comparison identity producer."""

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

app = create_app(
    Settings(remote=True, operator_token="owned-rsi-installed", remote_requests_per_minute=10000)
)


