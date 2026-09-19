"""The standalone example owns scanner and controller cleanup."""

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from .test_families import advertisement


@pytest.mark.parametrize("failure", [None, "update", "cancel", "scan-timeout"])
def test_example_releases_resources(monkeypatch, failure):
    spec = spec_from_file_location(
        "standalone_example", Path(__file__).parents[1] / "examples/run.py"
    )
    assert spec is not None and spec.loader is not None
    example = module_from_spec(spec)
    spec.loader.exec_module(example)
    scanner_stopped = Mock()

    class Scanner:
        def __init__(self, detection_callback):
            self.detected = detection_callback

        async def __aenter__(self):
            if failure != "scan-timeout":
                self.detected(
                    SimpleNamespace(address="test"),
                    SimpleNamespace(manufacturer_data={2306: bytes(advertisement(11))}),
                )
            return self

        async def __aexit__(self, *args):
            scanner_stopped()

    controller = Mock(
        update=AsyncMock(), refresh_telemetry=AsyncMock(), stop=AsyncMock()
    )
    if failure in ("update", "cancel"):
        controller.update.side_effect = (
            RuntimeError if failure == "update" else asyncio.CancelledError
        )
    factory = Mock(return_value=controller)
    monkeypatch.setattr(example, "BleakScanner", Scanner)
    monkeypatch.setattr(example, "ACInfinityController", factory)
    if failure:
        error = {
            "update": RuntimeError,
            "cancel": asyncio.CancelledError,
            "scan-timeout": TimeoutError,
        }[failure]
        with pytest.raises(error):
            asyncio.run(example.run(timeout=0.01))
    else:
        asyncio.run(example.run(timeout=0.01))
    scanner_stopped.assert_called_once()
    if failure == "scan-timeout":
        factory.assert_not_called()
    else:
        controller.stop.assert_awaited_once()
        controller.register_callback.return_value.assert_called_once()
