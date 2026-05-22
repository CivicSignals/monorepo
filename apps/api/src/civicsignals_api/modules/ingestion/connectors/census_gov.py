"""``census_gov`` — Census of Governments bulk import (doc 18 §1 cat. G, §5 wave 3
#18; D8).

The Census Bureau's Census of Governments enumerates ~90,000 US government
entities — states, counties, municipalities, townships, special districts, and
school districts — the **broad government entity universe** that underpins entity
resolution (doc 16 §6, §16: Tier 1 "foundational (entity directory)", public
domain). An *entity directory*, not a signal stream: normalize() resolves each row
to an Entity by its **FIPS code** (doc 18 §2.4 step 5 — the stable external id
path; doc 16 §13).

Mechanically a bulk CSV download with checkpointing (doc 18 §1 cat. G): a thin
:class:`BulkEntityDirectoryConnector` whose config defaults ``file_url`` to the
published government-units file. A recipe overrides ``file_url`` to pin a
census-year vintage (the Census of Governments runs every five years).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from ._bulk_entity_directory import BulkEntityDirectoryConfig, BulkEntityDirectoryConnector
from .base import register

# The government-units file from the most recent Census of Governments. Recipes
# override ``file_url`` to pin a census vintage. Source index:
# https://www.census.gov/programs-surveys/cog.html.
_DEFAULT_COG_URL = "https://www2.census.gov/programs-surveys/cog/2022/govt_units_2022.zip"


class CensusGovConfig(BulkEntityDirectoryConfig):
    """Per-recipe ``connector_config.census_gov`` (mirrors the JSON Schema)."""

    file_url: str = _DEFAULT_COG_URL


@register
class CensusGovConnector(BulkEntityDirectoryConnector):
    """Census of Governments entity-directory bulk import (doc 18 §1 cat. G, §5 wave 3 #18)."""

    name: ClassVar[str] = "census_gov"
    config_model: ClassVar[type[BaseModel] | None] = CensusGovConfig


__all__ = [
    "CensusGovConfig",
    "CensusGovConnector",
]
