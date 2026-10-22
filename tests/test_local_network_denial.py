from __future__ import annotations

import asyncio
import errno
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aegis_core.contracts import PolicyDecision, ToolAuthorization
from aegis_core.tools import smart_tv
from aegis_core.tools.defaults import default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


def deny_datagram_operation(
    monkeypatch: pytest.MonkeyPatch, stage: str, error: OSError
) -> MagicMock:
    udp = MagicMock()
    create = MagicMock(return_value=udp)
    send = AsyncMock()
    receive = AsyncMock(side_effect=TimeoutError)
    if stage == "open":
        create.side_effect = error
    elif stage == "configure":
        udp.setblocking.side_effect = error
    elif stage == "broadcast":
        udp.setsockopt.side_effect = error
    elif stage == "send":
        send.side_effect = error
    elif stage == "receive":
        receive.side_effect = error
    monkeypatch.setattr(smart_tv.socket, "socket", create)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "sock_sendto", send)
    monkeypatch.setattr(loop, "sock_recvfrom", receive)
    return udp


@pytest.mark.parametrize("error_type", [PermissionError, OSError])
@pytest.mark.parametrize("stage", ["open", "configure", "send", "receive"])
async def test_ssdp_denial_fails_closed_and_releases_socket(
    monkeypatch: pytest.MonkeyPatch, error_type: type[OSError], stage: str
) -> None:
    error = error_type(errno.EACCES, "synthetic private network diagnostic")
    udp = deny_datagram_operation(monkeypatch, stage, error)

    with pytest.raises(smart_tv.TargetDeviceOffline, match=r"^SSDP discovery failed$") as failed:
        await smart_tv.discover_smart_tvs(timeout_seconds=0.25)

    assert failed.value.__cause__ is error
    assert udp.close.call_count == (0 if stage == "open" else 1)


@pytest.mark.parametrize("error_type", [PermissionError, OSError])
@pytest.mark.parametrize("stage", ["open", "configure", "broadcast", "send"])
async def test_wake_on_lan_denial_fails_closed_and_releases_socket(
    monkeypatch: pytest.MonkeyPatch, error_type: type[OSError], stage: str
) -> None:
    error = error_type(errno.EPERM, "synthetic private network diagnostic")
    udp = deny_datagram_operation(monkeypatch, stage, error)

    with pytest.raises(smart_tv.TargetDeviceOffline, match=r"^Wake-on-LAN broadcast failed$"):
        await smart_tv.send_wake_on_lan("aa:bb:cc:dd:ee:ff")

    assert udp.close.call_count == (0 if stage == "open" else 1)


@pytest.mark.parametrize("stage", ["open", "send", "receive"])
async def test_ssdp_tool_returns_unverified_failure_when_network_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    deny_datagram_operation(monkeypatch, stage, PermissionError("private diagnostic"))
    service = smart_tv.SmartTVToolService(smart_tv.SmartTVController(()))
    authorization = ToolAuthorization(
        call_id="synthetic-discovery",
        tool_name=service.DISCOVERY_TOOL,
        call_digest="d" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="policy_allowed",
        normalized_arguments={"timeout_milliseconds": 250},
    )

    result = await service.discover(authorization, default_policy_context(tmp_path))

    assert result.success is False
    assert result.error_code == "device_discovery_failed"
    assert result.metadata == {"verified": False}
    assert "private diagnostic" not in result.model_dump_json()


async def test_ssdp_without_advertisements_returns_empty_and_closes_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    udp = deny_datagram_operation(monkeypatch, "none", OSError())

    assert await smart_tv.discover_smart_tvs(timeout_seconds=0.25) == ()
    udp.close.assert_called_once()


@pytest.mark.parametrize("error_type", [PermissionError, OSError])
async def test_tv_connection_probe_reports_offline_when_network_is_denied(
    monkeypatch: pytest.MonkeyPatch, error_type: type[OSError]
) -> None:
    connect = AsyncMock(side_effect=error_type(errno.EACCES, "private diagnostic"))
    monkeypatch.setattr(smart_tv.asyncio, "open_connection", connect)
    controller = smart_tv.SmartTVController((smart_tv.SmartTVProfile(
        device_id="synthetic-tv", platform=smart_tv.TVPlatform.ANDROID, host="192.168.1.50"
    ),))

    online, latency_ms = await controller.verify("synthetic-tv")

    assert online is False
    assert latency_ms >= 0
    connect.assert_awaited_once_with("192.168.1.50", 6466)


@pytest.mark.parametrize("error_type", [PermissionError, OSError])
@pytest.mark.parametrize("stage", ["open", "configure", "connect"])
@pytest.mark.parametrize("address", ["192.168.1.50", "fd00::50"])
def test_tcp_probe_denial_is_unreachable_and_closes_created_socket(
    monkeypatch: pytest.MonkeyPatch, error_type: type[OSError], stage: str, address: str
) -> None:
    error = error_type(errno.EACCES, "private diagnostic")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    create = MagicMock(return_value=connection)
    if stage == "open":
        create.side_effect = error
    elif stage == "configure":
        connection.settimeout.side_effect = error
    else:
        connection.connect_ex.side_effect = error
    monkeypatch.setattr("aegis_core.tools.execution.socket.socket", create)

    assert ReadOnlyToolExecutor._probe_tcp(address, 443, 0.25) == "unreachable"
    assert connection.__exit__.call_count == (0 if stage == "open" else 1)
