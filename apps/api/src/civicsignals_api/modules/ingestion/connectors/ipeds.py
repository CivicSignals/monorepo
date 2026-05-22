"""``ipeds`` — IPEDS higher-ed directory bulk import (doc 18 §1 cat. G, §5 wave 3
#17; D8).

IPEDS (the Integrated Postsecondary Education Data System) is NCES's directory of
all ~6,000 US higher-ed institutions — the **higher-ed entity universe** (doc 16
§6, §16: Tier 1, public domain; data access at
``https://nces.ed.gov/ipeds/use-the-data``). An *entity directory*, not a signal
stream: normalize() resolves each row to a higher-ed Entity by its **IPEDS unit
id** (doc 18 §2.4 step 5 — the stable external id path; doc 16 §13).

Mechanically a bulk CSV download with checkpointing (doc 18 §1 cat. G): a thin
:class:`BulkEntityDirectoryConnector` whose config defaults ``file_url`` to the
current IPEDS institutional-directory (HD) file. A recipe overrides ``file_url``
to pin a survey year.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from ._bulk_entity_directory import BulkEntityDirectoryConfig, BulkEntityDirectoryConnector
from .base import register

# The IPEDS institutional characteristics directory (HD) file for a survey year.
# Recipes override ``file_url`` to pin a year. Source index:
# https://nces.ed.gov/ipeds/use-the-data.
_DEFAULT_IPEDS_URL = "https://nces.ed.gov/ipeds/datacenter/data/HD2022.zip"


class IpedsConfig(BulkEntityDirectoryConfig):
    """Per-recipe ``connector_config.ipeds`` (mirrors the JSON Schema)."""

    file_url: str = _DEFAULT_IPEDS_URL


@register
class IpedsConnector(BulkEntityDirectoryConnector):
    """IPEDS higher-ed entity-directory bulk import (doc 18 §1 cat. G, §5 wave 3 #17)."""

    name: ClassVar[str] = "ipeds"
    config_model: ClassVar[type[BaseModel] | None] = IpedsConfig


__all__ = [
    "IpedsConfig",
    "IpedsConnector",
]
