"""Registered discovery adapters, separate from acquisition admission.

An adapter owns revision(), packages(revision) -> {package: tree_hash},
package_url(), preview_info(), metadata(inventory=...), file() and close().
Enumeration must stay lightweight; preview_info must not fetch full detail documents.
Factories accept cancel/progress/notice/rate_state; outbound URLs remain adapter-owned.
"""

import re
from dataclasses import dataclass

from packages.catalog.source import DATASET, RateState, SimReadySource
from packages.library.common import LibraryError


@dataclass(frozen=True)
class CatalogSource:
    provider: str
    dataset: str
    factory: object


class CatalogSources:
    def __init__(self, registrations=None, *, simready_factory=None):
        registrations = (
            registrations
            if registrations is not None
            else [CatalogSource("simready", DATASET, simready_factory or SimReadySource)]
        )
        self.registrations = {}
        for item in registrations:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", item.provider):
                raise ValueError("Invalid catalog provider identity")
            if item.provider in self.registrations or not item.dataset or not callable(item.factory):
                raise ValueError("Duplicate or invalid catalog source registration")
            self.registrations[item.provider] = item
        if len(self.registrations) > 100:
            raise ValueError("At most 100 catalog sources may be registered")
        self.rates = {provider: RateState() for provider in self.registrations}

    def require(self, provider):
        if provider not in self.registrations:
            raise LibraryError("unknown_catalog_source", "Catalog source is not registered", 404)
        return self.registrations[provider]

    def open(self, provider, **kwargs):
        registration = self.require(provider)
        return registration.factory(rate_state=self.rates[provider], **kwargs)
