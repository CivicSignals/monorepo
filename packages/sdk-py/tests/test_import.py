"""Smoke test: the SDK imports and exposes the client."""

from civicsignals_sdk import CivicSignalsClient, CivicSignalsError


def test_exports() -> None:
    assert CivicSignalsClient is not None
    assert issubclass(CivicSignalsError, Exception)
