"""CivicSignals backend modulith.

A single deployable FastAPI application partitioned into bounded modules
(see ``civicsignals_api.modules``). The same image runs as the API process or
as one of the Celery worker / scheduler processes — selected by the container
command. See doc 06 §1, §3, §10 and doc 18 §6.
"""

__version__ = "0.1.0"
