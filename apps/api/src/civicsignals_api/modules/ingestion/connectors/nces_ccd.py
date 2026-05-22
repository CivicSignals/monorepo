"""``nces_ccd`` — NCES Common Core of Data bulk import (doc 18 §1 cat. G, §5 wave 3
#16; D8).

The NCES Common Core of Data is the federal directory of every US public school
district and school — the **K-12 entity universe** (doc 16 §6, §16: Tier 1, public
domain; data files at ``https://nces.ed.gov/ccd/ccddata.asp``). It is an *entity
directory*, not a signal stream: normalize() resolves each row to a K-12 Entity by
its **NCES id** (doc 18 §2.4 step 5 — the stable external id path; doc 16 §13).

Mechanically a bulk CSV download with checkpointing (doc 18 §1 cat. G): a thin
:class:`BulkEntityDirectoryConnector` whose config defaults ``file_url`` to the
current CCD directory file. A recipe overrides ``file_url`` to pin a specific
school-year vintage.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from ._bulk_entity_directory import BulkEntityDirectoryConfig, BulkEntityDirectoryConnector
from .base import register

# The CCD nonfiscal directory file (school-year vintage). Recipes override
# ``file_url`` to pin a specific year; this default keeps the connector usable
# out of the box. Source index: https://nces.ed.gov/ccd/ccddata.asp.
_DEFAULT_CCD_URL = "https://nces.ed.gov/ccd/data/zip/ccd_lea_029_2223_w_1a_073123.zip"


class NcesCcdConfig(BulkEntityDirectoryConfig):
    """Per-recipe ``connector_config.nces_ccd`` (mirrors the JSON Schema)."""

    file_url: str = _DEFAULT_CCD_URL


@register
class NcesCcdConnector(BulkEntityDirectoryConnector):
    """NCES CCD K-12 entity-directory bulk import (doc 18 §1 cat. G, §5 wave 3 #16)."""

    name: ClassVar[str] = "nces_ccd"
    config_model: ClassVar[type[BaseModel] | None] = NcesCcdConfig


__all__ = [
    "NcesCcdConfig",
    "NcesCcdConnector",
]
