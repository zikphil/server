"""
Tests for addressing the zones of a multi-zone Sonos amplifier.

A Sonos Amp Multi hosts up to four players on one IP address. Each zone announces its
own player id and its own local API port (1443, 1444, ...). Asking the default port for
every zone binds all of them to the first zone, so the provider must use the announced
port when it asks a zone who it is and when the player connects.
"""

import asyncio
import logging
import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from music_assistant_models.enums import IdentifierType
from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceInfo

from music_assistant.models.player import DeviceInfo
from music_assistant.models.player_provider import PlayerProvider
from music_assistant.providers.sonos import provider as provider_module
from music_assistant.providers.sonos.player import SonosPlayer
from music_assistant.providers.sonos.provider import SonosPlayerProvider

AMP_IP = "192.168.2.17"
ZONES = {
    # port -> (player id, zone name), as one Amp Multi announces them
    1443: ("RINCON_804AF2304E8C01400", "Kitchen"),
    1444: ("RINCON_804AF2304E8C01500", "Guest Bathroom"),
    1445: ("RINCON_804AF2304E8C01600", "Bedroom"),
    1446: ("RINCON_804AF2304E8C01700", "Main Bathroom"),
}


def _announcement(port: int) -> AsyncServiceInfo:
    """Build the mDNS record one zone of the amplifier announces."""
    player_id, name = ZONES[port]
    return AsyncServiceInfo(
        "_sonos._tcp.local.",
        f"{player_id}@{name}._sonos._tcp.local.",
        port=port,
        properties={b"uuid": player_id.encode(), b"sslport": str(port).encode()},
        server="Sonos-804AF2304E8C.local.",
        addresses=[socket.inet_aton(AMP_IP)],
    )


def _discovery_info(port: int) -> dict[str, Any]:
    """Return what /players/local/info answers on the given port of the amplifier."""
    player_id, name = ZONES[port]
    return {
        "playerId": player_id,
        "householdId": "Sonos_hh",
        "groupId": f"{player_id}:1",
        "websocketUrl": f"wss://{AMP_IP}:{port}/websocket/api",
        "restUrl": f"https://{AMP_IP}:{port}/api",
        "device": {
            "id": player_id,
            "name": name,
            "modelDisplayName": "Amp Multi",
            "capabilities": ["PLAYBACK", "CLOUD", "AIRPLAY", "AUDIO_CLIP"],
        },
    }


async def _fake_get_discovery_info(_session: object, ip: str, port: int = 1443) -> dict[str, Any]:
    """Answer like the amplifier does: each port is a different zone."""
    assert ip == AMP_IP
    return _discovery_info(port)


def _make_provider() -> tuple[SonosPlayerProvider, MagicMock]:
    """Create a Sonos provider with mocked discovery dependencies."""
    mass = MagicMock()
    mass.config.get_raw_player_config_value.return_value = True
    mass.players.get_player.return_value = None
    provider = SonosPlayerProvider.__new__(SonosPlayerProvider)
    provider.mass = mass
    provider.logger = logging.getLogger("test.sonos.multizone")
    provider._ignored_disabled_players = set()
    provider._pending_setup_tasks = set()
    provider._pending_refresh_tasks = set()
    provider._unloaded = False
    return provider, mass


@pytest.mark.asyncio
async def test_each_zone_is_queried_and_created_on_its_own_port() -> None:
    """Every zone is asked who it is on its announced port and keeps that port."""
    provider, _mass = _make_provider()
    created: list[MagicMock] = []

    def _fake_player(
        _prov: object, player_id: str, discovery_info: dict[str, Any], port: int
    ) -> MagicMock:
        player = MagicMock(spec=SonosPlayer)
        player.player_id = player_id
        player.discovery_info = discovery_info
        player.api_port = port
        player.device_info = DeviceInfo()
        player.setup = AsyncMock()
        created.append(player)
        return player

    with (
        patch.object(provider_module, "get_discovery_info", side_effect=_fake_get_discovery_info),
        patch.object(provider_module, "SonosPlayer", side_effect=_fake_player),
    ):
        for port in ZONES:
            info = _announcement(port)
            await provider._setup_player(ZONES[port][0], ZONES[port][1], info)

    assert [player.player_id for player in created] == [zone[0] for zone in ZONES.values()]
    assert [player.api_port for player in created] == list(ZONES)
    # the name comes from the zone itself, not from the first zone answering on 1443
    assert [player.discovery_info["device"]["name"] for player in created] == [
        zone[1] for zone in ZONES.values()
    ]
    for player in created:
        assert player.device_info.ip_address == AMP_IP
        player.setup.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_reannounced_zone_reconnects_on_its_own_port() -> None:
    """A zone that comes back online reconnects its client to its own port."""
    provider, mass = _make_provider()
    player = MagicMock(spec=SonosPlayer)
    player.player_id = ZONES[1446][0]
    player.display_name = ZONES[1446][1]
    player.connected = False
    player.api_port = 1443  # remembered wrongly, e.g. from an older version
    player.device_info = DeviceInfo()
    player.device_info.add_identifier(IdentifierType.IP_ADDRESS, AMP_IP)
    player.client = MagicMock()
    player.logger = logging.getLogger("test.sonos.multizone.player")
    mass.players.get_player.return_value = player

    await provider.on_mdns_service_state_change(
        f"{ZONES[1446][0]}@Main Bathroom._sonos._tcp.local.",
        ServiceStateChange.Updated,
        _announcement(1446),
    )

    assert player.api_port == 1446
    assert player.client.player_ip == AMP_IP
    assert player.client.player_port == 1446
    player.reconnect.assert_called_once()


@pytest.mark.asyncio
async def test_manual_address_may_carry_the_zone_port() -> None:
    """A manually configured zone is addressed as host:port."""
    provider, _mass = _make_provider()
    provider.config = MagicMock()
    provider.config.get_value.return_value = [f"{AMP_IP}:1445"]
    created: list[MagicMock] = []

    def _fake_player(
        _prov: object, player_id: str, discovery_info: dict[str, Any], port: int
    ) -> MagicMock:
        player = MagicMock(spec=SonosPlayer)
        player.player_id = player_id
        player.discovery_info = discovery_info
        player.api_port = port
        player.device_info = DeviceInfo()
        player.setup = AsyncMock()
        created.append(player)
        return player

    with (
        patch.object(PlayerProvider, "loaded_in_mass", AsyncMock()),
        patch.object(provider_module, "get_discovery_info", side_effect=_fake_get_discovery_info),
        patch.object(provider_module, "SonosPlayer", side_effect=_fake_player),
    ):
        await provider.loaded_in_mass()

    assert len(created) == 1
    assert created[0].player_id == ZONES[1445][0]
    assert created[0].api_port == 1445
    assert created[0].device_info.ip_address == AMP_IP


def _bare_player(port: int) -> tuple[SonosPlayer, MagicMock]:
    """Create a SonosPlayer for one zone without running the full constructor."""
    player = SonosPlayer.__new__(SonosPlayer)
    mass = MagicMock()
    player.mass = mass
    player.logger = logging.getLogger("test.sonos.multizone.player")
    player._player_id = ZONES[port][0]
    player.api_port = port
    player.discovery_info = _discovery_info(port)  # type: ignore[assignment]
    player._attr_device_info = DeviceInfo()
    player._attr_device_info.add_identifier(IdentifierType.IP_ADDRESS, AMP_IP)
    player._connect_lock = asyncio.Lock()
    return player, mass


@pytest.mark.asyncio
async def test_player_connects_its_client_to_its_own_port() -> None:
    """The player's API client is created for the zone's port, not the default."""
    player, mass = _bare_player(1444)
    player._connect = AsyncMock(side_effect=RuntimeError("stop after the client is created"))  # type: ignore[method-assign]

    with (
        patch("music_assistant.providers.sonos.player.SonosLocalApiClient") as client_cls,
        pytest.raises(RuntimeError),
    ):
        await player.setup()

    client_cls.assert_called_once_with(AMP_IP, mass.http_session_no_ssl, port=1444)


@pytest.mark.asyncio
async def test_reachability_probes_the_zone_port() -> None:
    """The reachability probe knocks on the zone's own port."""
    player, _mass = _bare_player(1446)
    writer = MagicMock()
    writer.wait_closed = AsyncMock()

    with patch(
        "music_assistant.providers.sonos.player.asyncio.open_connection",
        AsyncMock(return_value=(MagicMock(), writer)),
    ) as open_connection:
        assert await player._is_reachable() is True

    open_connection.assert_awaited_once_with(AMP_IP, 1446)


def test_zone_mac_addresses_are_unique_per_zone() -> None:
    """Each zone derives the MAC address its own AirPlay endpoint advertises."""
    macs = [_bare_player(port)[0]._extract_mac_from_player_id() for port in ZONES]

    assert macs == [
        "80:4A:F2:30:4E:8C",
        "80:4A:F2:30:4E:8D",
        "80:4A:F2:30:4E:8E",
        "80:4A:F2:30:4E:8F",
    ]
