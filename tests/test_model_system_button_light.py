# SPDX-License-Identifier: MIT
# Copyright (c) 2021-2026
"""Test the HAP profile through the existing central and XML-RPC fixtures."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from aiohomematic.central import CentralUnit
from aiohomematic.central.events import DataPointStateChangedEvent
from aiohomematic.client import InterfaceConfig
from aiohomematic.const import (
    CallSource,
    DataPointCategory,
    DataPointUsage,
    Interface,
    Parameter,
    ParamsetKey,
    SourceOfDeviceCreation,
)
from aiohomematic.exceptions import AioHomematicException
from aiohomematic.interfaces import DeviceProtocol
from aiohomematic.model.custom import CustomDpSystemButtonLight
from aiohomematic.model.generic import DpFloat
from aiohomematic_test_support import const
from aiohomematic_test_support.factory import FactoryWithClient

# pylint: disable=protected-access,redefined-outer-name


@dataclass
class HapFixture:
    """Keep the real device and the two mocked wire methods together."""

    central: CentralUnit
    device: DeviceProtocol
    state: dict[str, Any]
    read: AsyncMock
    write: AsyncMock

    @property
    def light(self) -> CustomDpSystemButtonLight:
        """Return the discovered custom light."""
        light = self.device.get_custom_data_point(channel_no=0)
        assert isinstance(light, CustomDpSystemButtonLight)
        return light

    @property
    def signal(self) -> DpFloat:
        """Return the underlying MASTER data point."""
        dp = self.device.get_generic_data_point(
            channel_address=f"{self.device.address}:0",
            parameter=Parameter.SIGNAL_BRIGHTNESS,
            paramset_key=ParamsetKey.MASTER,
        )
        assert isinstance(dp, DpFloat)
        return dp


@pytest.fixture
async def hap(factory_with_ccu_client: FactoryWithClient, request: pytest.FixtureRequest) -> AsyncGenerator[HapFixture]:
    """Extend the existing factory with synthetic, serial-independent HAP metadata."""
    options = getattr(request, "param", {})
    address = options.get("address", "VCU9000001")
    model = options.get("model", "HmIP-HAP JS1")
    channel_no = options.get("channel_no", 0)
    channel_address = f"{address}:{channel_no}"
    paramset = options.get("paramset", ParamsetKey.MASTER)
    description = (
        {
            Parameter.SIGNAL_BRIGHTNESS: {
                "TYPE": "FLOAT",
                "MIN": 0.0,
                "MAX": 1.0,
                "DEFAULT": 0.0,
                "OPERATIONS": options.get("operations", 3),
                "FLAGS": 1,
            },
        }
        if options.get("has_parameter", True)
        else {}
    )
    values_description = {
        Parameter.CONFIG_PENDING: {
            "TYPE": "BOOL",
            "MIN": False,
            "MAX": True,
            "DEFAULT": False,
            "OPERATIONS": 5,
            "FLAGS": 1,
        },
        Parameter.UN_REACH: {"TYPE": "BOOL", "MIN": False, "MAX": True, "DEFAULT": False, "OPERATIONS": 5, "FLAGS": 1},
        Parameter.DUTY_CYCLE: {"TYPE": "FLOAT", "MIN": 0.0, "MAX": 1.0, "DEFAULT": 0.0, "OPERATIONS": 5, "FLAGS": 1},
    }
    if paramset == ParamsetKey.VALUES:
        description.update(values_description)
    factory = factory_with_ccu_client.init(
        address_device_translation=set(),
        do_mock_client=False,
        interface_configs={InterfaceConfig(central_name=const.CENTRAL_NAME, interface=Interface.HMIP_RF, port=2010)},
    )
    central = await factory.get_default_central()
    client = central.client_coordinator.primary_client
    assert client is not None
    interface_id = client.interface_id
    cache = central.cache_coordinator
    cache.device_descriptions.add_device(
        interface_id=interface_id,
        device_description={
            "ADDRESS": address,
            "TYPE": model,
            "CHILDREN": [channel_address],
            "PARAMSETS": [],
            "RX_MODE": 1,
            "FIRMWARE": "3.0.18",
        },
    )
    cache.device_descriptions.add_device(
        interface_id=interface_id,
        device_description={
            "ADDRESS": channel_address,
            "TYPE": options.get("channel_type", "MAINTENANCE"),
            "PARENT": address,
            "INDEX": channel_no,
            "PARAMSETS": [paramset, ParamsetKey.VALUES],
        },
    )
    cache.paramset_descriptions.add(
        interface_id=interface_id,
        channel_address=channel_address,
        paramset_key=paramset,
        paramset_description=description,
        device_type=model,
    )
    if paramset != ParamsetKey.VALUES:
        cache.paramset_descriptions.add(
            interface_id=interface_id,
            channel_address=channel_address,
            paramset_key=ParamsetKey.VALUES,
            paramset_description=values_description,
            device_type=model,
        )
    state = {Parameter.SIGNAL_BRIGHTNESS: options.get("value", 0.0)}
    if state[Parameter.SIGNAL_BRIGHTNESS] is None:
        state.clear()

    async def read_paramset(*args: Any) -> dict[str, Any]:
        return dict(state)

    async def write_paramset(*args: Any) -> None:
        state.update(args[2])

    read = AsyncMock(side_effect=read_paramset)
    write = AsyncMock(side_effect=write_paramset)
    try:
        with (
            patch.object(factory._xml_proxy, "getParamset", read),
            patch.object(factory._xml_proxy, "putParamset", write),
            patch.object(
                factory._xml_proxy,
                "getParamsetDescription",
                AsyncMock(side_effect=lambda address, key: description if key == paramset else values_description),
            ),
        ):
            await central.device_coordinator.create_devices(
                new_device_addresses={interface_id: {address}}, source=SourceOfDeviceCreation.INIT
            )
            device = central.device_coordinator.get_device(address=address)
            assert device is not None
            yield HapFixture(central=central, device=device, state=state, read=read, write=write)
    finally:
        factory.cleanup()
        await central.stop()


@pytest.mark.parametrize(
    ("hap", "expected"),
    [({"value": 0.0}, False), ({"value": 1.0}, True), ({"value": 0.5}, True), ({"value": None}, None)],
    indirect=["hap"],
)
async def test_discovery_and_state(hap: HapFixture, expected: bool | None) -> None:
    """Discover an on/off custom light without rewriting intermediate or unknown values."""
    assert hap.light.category == DataPointCategory.LIGHT
    assert hap.light.usage == DataPointUsage.CDP_PRIMARY
    assert hap.light.is_on is expected
    assert hap.light.available is True
    assert hap.light.unique_id == f"{hap.device.address.lower()}_0"
    assert hap.signal.usage == DataPointUsage.NO_CREATE
    assert not hasattr(hap.light, "brightness")
    hap.write.assert_not_awaited()
    hap.read.assert_awaited_with(f"{hap.device.address}:0", ParamsetKey.MASTER)


@pytest.mark.parametrize(
    ("hap", "command", "target", "writes"),
    [
        ({"value": 0.0}, "turn_on", 1.0, 1),
        ({"value": 1.0}, "turn_off", 0.0, 1),
        ({"value": 1.0}, "turn_on", 1.0, 0),
        ({"value": 0.0}, "turn_off", 0.0, 0),
        ({"value": 0.5}, "turn_on", 1.0, 1),
        ({"value": 0.5}, "turn_off", 0.0, 1),
        ({"value": None}, "turn_on", 1.0, 1),
        ({"value": None}, "turn_off", 0.0, 1),
    ],
    indirect=["hap"],
)
async def test_commands(hap: HapFixture, command: str, target: float, writes: int) -> None:
    """Use actual putParamset/getParamset routing and avoid confirmed endpoint writes."""
    hap.read.reset_mock()
    await getattr(hap.light, command)()
    assert hap.write.await_count == writes
    if writes:
        hap.write.assert_awaited_once_with(
            f"{hap.device.address}:0", ParamsetKey.MASTER, {Parameter.SIGNAL_BRIGHTNESS: target}
        )
        hap.read.assert_awaited_once_with(f"{hap.device.address}:0", ParamsetKey.MASTER)
    else:
        hap.read.assert_not_awaited()
    assert hap.signal.value == target
    assert hap.light.is_on is (target > 0.0)
    assert hap.signal.is_optimistic is False


@pytest.mark.parametrize(
    "hap",
    [
        {"model": "HmIP-OTHER"},
        {"model": "HmIP-HAP JS1-OTHER"},
        {"model": "HmIP-HAP"},
        {"channel_no": 1},
        {"channel_type": "DIMMER"},
        {"paramset": ParamsetKey.VALUES},
        {"has_parameter": False},
        {"operations": 1},
    ],
    indirect=True,
)
async def test_profile_isolation(hap: HapFixture) -> None:
    """Require the exact model, channel, channel type and writable MASTER float."""
    assert not hap.device.custom_data_points
    hap.write.assert_not_awaited()


@pytest.mark.parametrize("hap", [{"address": "VCU9000002"}], indirect=True)
async def test_serial_independent_detection(hap: HapFixture) -> None:
    """Discover the same profile for a different synthetic address."""
    assert isinstance(hap.light, CustomDpSystemButtonLight)


async def test_refresh_and_configuration_panel(hap: HapFixture) -> None:
    """Preserve panel validation and publish existing MASTER reloads to the light."""
    updated = Mock()
    hap.light.register()
    unsubscribe = hap.central.event_bus.subscribe(
        event_type=DataPointStateChangedEvent, event_key=hap.light.unique_id, handler=updated
    )
    try:
        for value in (1.0, 0.0, 0.5):
            result = await hap.central.configuration.put_paramset(
                interface_id=hap.device.interface_id,
                channel_address=f"{hap.device.address}:0",
                paramset_key=ParamsetKey.MASTER,
                values={Parameter.SIGNAL_BRIGHTNESS: value},
            )
            assert result.success is True
            values = await hap.central.configuration.get_paramset(
                interface_id=hap.device.interface_id,
                channel_address=f"{hap.device.address}:0",
                paramset_key=ParamsetKey.MASTER,
            )
            assert values[Parameter.SIGNAL_BRIGHTNESS] == value
            await hap.device.on_config_changed()
            assert hap.signal.value == value
            assert hap.light.is_on is (value > 0.0)
        await hap.central.looper.block_till_done()
        assert updated.call_count >= 3
        assert hap.state[Parameter.SIGNAL_BRIGHTNESS] == 0.5
        rejected = await hap.central.configuration.put_paramset(
            interface_id=hap.device.interface_id,
            channel_address=f"{hap.device.address}:0",
            paramset_key=ParamsetKey.MASTER,
            values={Parameter.SIGNAL_BRIGHTNESS: 1.1},
        )
        assert rejected.success is False
    finally:
        unsubscribe()


async def test_write_failure_keeps_confirmed_state(hap: HapFixture) -> None:
    """Keep the client's logged failure behavior without optimistic state changes."""
    hap.write.side_effect = AioHomematicException("write failed")
    hap.read.reset_mock()
    await hap.light.turn_on()
    hap.read.assert_not_awaited()
    assert hap.light.is_on is False
    assert hap.signal.value == 0.0


async def test_uncertain_endpoint_is_written(hap: HapFixture) -> None:
    """Do not deduplicate a cached value after a failed refresh."""
    hap.read.side_effect = AioHomematicException("read failed")
    await hap.signal.load_data_point_value(call_source=CallSource.MANUAL_OR_SCHEDULED, direct_call=True)
    assert hap.signal.state_uncertain is True
    hap.read.side_effect = lambda *args: dict(hap.state)
    await hap.light.turn_off()
    hap.write.assert_awaited_once_with(
        f"{hap.device.address}:0", ParamsetKey.MASTER, {Parameter.SIGNAL_BRIGHTNESS: 0.0}
    )


async def test_existing_configuration_callback_and_availability(hap: HapFixture) -> None:
    """Use real VALUES callbacks for config refresh and normal device availability."""
    diagnostic = hap.device.get_generic_data_point(
        channel_address=f"{hap.device.address}:0", parameter=Parameter.DUTY_CYCLE
    )
    assert diagnostic is not None
    assert diagnostic.usage != DataPointUsage.NO_CREATE
    event = hap.central.event_coordinator.data_point_event
    event_args = {"interface_id": hap.device.interface_id, "channel_address": f"{hap.device.address}:0"}
    await event(**event_args, parameter=Parameter.CONFIG_PENDING, value=True)
    hap.state[Parameter.SIGNAL_BRIGHTNESS] = 0.5
    await event(**event_args, parameter=Parameter.CONFIG_PENDING, value=False)
    await hap.central.looper.block_till_done()
    assert hap.signal.value == 0.5
    assert hap.light.is_on is True
    await event(**event_args, parameter=Parameter.UN_REACH, value=True)
    assert hap.light.available is False
    await event(**event_args, parameter=Parameter.UN_REACH, value=False)
    assert hap.light.available is True


async def test_readback_failure_keeps_uncertain_confirmed_state(hap: HapFixture) -> None:
    """Keep the last confirmed value uncertain if an accepted write cannot be read."""
    hap.read.side_effect = AioHomematicException("read failed")
    await hap.light.turn_on()
    assert hap.state[Parameter.SIGNAL_BRIGHTNESS] == 1.0
    assert hap.signal.value == 0.0
    assert hap.signal.state_uncertain is True
    assert hap.signal.is_optimistic is False
    assert hap.light.is_on is False
