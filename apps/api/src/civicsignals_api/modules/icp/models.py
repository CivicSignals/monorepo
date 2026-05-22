"""icp SQLAlchemy models.

Tables are prefixed `icp_` and are migrated only by this module
(doc 06 §3, §4). Concrete models land in later epics; importing `Base` keeps
Alembic autogenerate aware of this module.
"""

from __future__ import annotations

from civicsignals_api.db import Base  # noqa: F401  (re-exported for Alembic discovery)
