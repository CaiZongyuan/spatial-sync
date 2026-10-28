"""Actual Synthetic transport fixture using the production comparison identity producer."""

import os

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

os.environ.pop("SPATIAL_CATALOG_METADATA_ROOT", None)
os.environ["SPATIAL_CATALOG_ROOT"] = ""

app = create_app(
    Settings(remote=True, operator_token="owned-rsi-installed", remote_requests_per_minute=10000)
)


