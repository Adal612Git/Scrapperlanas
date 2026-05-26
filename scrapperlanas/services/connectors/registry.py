from __future__ import annotations

from .ashby import AshbyConnector
from .base import BaseConnector
from .greenhouse import GreenhouseConnector
from .hn_algolia import HNAlgoliaConnector
from .lever import LeverConnector
from .ted_eu import TEDEUConnector
from .uk_contracts_finder import UKContractsFinderConnector
from .uk_find_tender import UKFindTenderConnector
from .workable import WorkableConnector
from .worldbank import WorldBankProcurementConnector


def connector_registry() -> dict[str, BaseConnector]:
    return {
        "hn_algolia": HNAlgoliaConnector(),
        "greenhouse": GreenhouseConnector(),
        "greenhouse_jobs": GreenhouseConnector(),
        "lever": LeverConnector(),
        "lever_postings": LeverConnector(),
        "ashby": AshbyConnector(),
        "ashby_jobs": AshbyConnector(),
        "workable": WorkableConnector(),
        "workable_jobs": WorkableConnector(),
        "ted_eu": TEDEUConnector(),
        "uk_find_tender": UKFindTenderConnector(),
        "uk_contracts_finder": UKContractsFinderConnector(),
        "worldbank_procurement": WorldBankProcurementConnector(),
    }


def connector_for_provider(provider: str) -> BaseConnector | None:
    return connector_registry().get(str(provider or "").strip().lower())
