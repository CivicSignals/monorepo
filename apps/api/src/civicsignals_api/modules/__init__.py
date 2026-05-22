"""Bounded modules of the CivicSignals modulith (doc 06 §3).

No module imports another module's internals; cross-module calls go through the
target module's ``services.py``. Each module owns its tables (prefixed
``<module>_``) and is the only module that migrates them.
"""
