"""Tests for the Sonos (S2) provider helpers."""

import socket

import pytest
from zeroconf.asyncio import AsyncServiceInfo

from music_assistant.providers.sonos.helpers import (
    get_local_api_port,
    get_primary_ip_address,
    mac_address_from_player_id,
    parse_manual_address,
    zone_index_from_player_id,
)

AMP_IP = "192.168.2.17"


def _announcement(
    player_id: str, name: str, port: int, *, sslport: str | None = None
) -> AsyncServiceInfo:
    """Build the mDNS record a Sonos S2 player (or one zone of an Amp Multi) announces."""
    properties: dict[bytes, bytes] = {b"uuid": player_id.encode()}
    if sslport is not None:
        properties[b"sslport"] = sslport.encode()
    return AsyncServiceInfo(
        "_sonos._tcp.local.",
        f"{player_id}@{name}._sonos._tcp.local.",
        port=port,
        properties=properties,
        server="Sonos-804AF2304E8C.local.",
        addresses=[socket.inet_aton(AMP_IP)],
    )


def test_local_api_port_prefers_sslport_property() -> None:
    """The zone's sslport TXT property names the port its local API listens on."""
    info = _announcement("RINCON_804AF2304E8C01700", "Main Bathroom", 1446, sslport="1446")

    assert get_local_api_port(info) == 1446
    assert get_primary_ip_address(info) == AMP_IP


def test_local_api_port_falls_back_to_srv_port() -> None:
    """Without the TXT property the SRV port is used."""
    info = _announcement("RINCON_804AF2304E8C01500", "Guest Bathroom", 1444)

    assert get_local_api_port(info) == 1444


def test_local_api_port_ignores_unreadable_sslport() -> None:
    """An unreadable sslport value falls through to the SRV port."""
    info = _announcement("RINCON_804AF2304E8C01600", "Bedroom", 1445, sslport="n/a")

    assert get_local_api_port(info) == 1445


def test_local_api_port_defaults_to_1443() -> None:
    """A record without any port information gets the single-zone default."""
    info = AsyncServiceInfo(
        "_sonos._tcp.local.",
        "RINCON_48A6B8B0FF3301400@Living Room._sonos._tcp.local.",
        properties={b"uuid": b"RINCON_48A6B8B0FF3301400"},
    )

    assert get_local_api_port(info) == 1443


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("192.168.2.135", ("192.168.2.135", 1443)),
        (" 192.168.2.17:1446 ", ("192.168.2.17", 1446)),
        ("[2001:db8::17]:1444", ("2001:db8::17", 1444)),
        ("2001:db8::17", ("2001:db8::17", 1443)),
        ("sonos-kitchen.local", ("sonos-kitchen.local", 1443)),
        ("sonos-kitchen.local:1445", ("sonos-kitchen.local", 1445)),
    ],
)
def test_parse_manual_address(value: str, expected: tuple[str, int]) -> None:
    """A manual address may carry the port of a zone, and defaults to 1443 without one."""
    assert parse_manual_address(value) == expected


@pytest.mark.parametrize(
    ("player_id", "expected"),
    [
        ("RINCON_804AF2304E8C01400", 0),
        ("RINCON_804AF2304E8C01500", 1),
        ("RINCON_804AF2304E8C01600", 2),
        ("RINCON_804AF2304E8C01700", 3),
        ("RINCON_48A6B8B0FF3301400", 0),
        # not a zone port: odd suffixes and short ids are treated as single-zone
        ("RINCON_804AF2304E8C01450", 0),
        ("RINCON_804AF2304E8C", 0),
        ("sonos_player", 0),
    ],
)
def test_zone_index_from_player_id(player_id: str, expected: int) -> None:
    """The trailing UPnP port of the id encodes the zone of a multi-zone amplifier."""
    assert zone_index_from_player_id(player_id) == expected


@pytest.mark.parametrize(
    ("player_id", "expected"),
    [
        # a single-zone speaker keeps the MAC address embedded in its id
        ("RINCON_48A6B8B0FF3301400", "48:A6:B8:B0:FF:33"),
        ("RINCON_804AF2304E8C01400", "80:4A:F2:30:4E:8C"),
        # the zones of an Amp Multi match the ids their AirPlay endpoints advertise
        ("RINCON_804AF2304E8C01500", "80:4A:F2:30:4E:8D"),
        ("RINCON_804AF2304E8C01600", "80:4A:F2:30:4E:8E"),
        ("RINCON_804AF2304E8C01700", "80:4A:F2:30:4E:8F"),
        # the offset never wraps into the next octet
        ("RINCON_804AF2304EFF01500", "80:4A:F2:30:4E:FF"),
        ("RINCON_804AF2304E8C", None),
        ("RINCON_ZZZZZZZZZZZZ01400", None),
    ],
)
def test_mac_address_from_player_id(player_id: str, expected: str | None) -> None:
    """The MAC address derived from a player id is unique per zone."""
    assert mac_address_from_player_id(player_id) == expected
