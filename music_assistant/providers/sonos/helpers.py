"""Helpers for the Sonos (S2) Provider."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from aiosonos.const import DEFAULT_LOCAL_API_PORT
from zeroconf import IPVersion

if TYPE_CHECKING:
    from zeroconf.asyncio import AsyncServiceInfo

# Sonos player ids look like RINCON_<12 hex chars of the MAC><5 digit UPnP port>, e.g.
# RINCON_804AF2304E8C01400. A single-zone speaker always ends in 01400. A multi-zone
# product such as the Sonos Amp Multi hosts one player per zone on the same IP address
# and gives each zone its own port: 01400, 01500, 01600, 01700 for UPnP and 1443, 1444,
# 1445, 1446 for the local API. Its AirPlay endpoints follow the same scheme with the
# last octet of the MAC address incremented per zone (…:8C, …:8D, …:8E, …:8F).
_BASE_UPNP_PORT = 1400
_ZONE_PORT_STEP = 100
_MAX_ZONES = 16


def get_primary_ip_address(discovery_info: AsyncServiceInfo) -> str | None:
    """Get primary IP address from zeroconf discovery info."""
    for address in discovery_info.ip_addresses_by_version(IPVersion.V4Only):
        if address.is_loopback or address.is_link_local or address.is_unspecified:
            continue
        return str(address)
    # fall back to IPv6 addresses if no usable IPv4 address found
    for address in discovery_info.ip_addresses_by_version(IPVersion.V6Only):
        if address.is_loopback or address.is_link_local or address.is_unspecified:
            continue
        return str(address)
    return None


def get_local_api_port(discovery_info: AsyncServiceInfo) -> int:
    """
    Get the port of the player's local API from its zeroconf discovery info.

    A single-zone speaker always listens on 1443. A multi-zone product (Sonos Amp Multi)
    runs one player per zone on the same IP address, each on its own port, which the zone
    announces both as the SRV port and in the ``sslport`` TXT property.
    """
    sslport = discovery_info.decoded_properties.get("sslport")
    if sslport:
        try:
            return int(sslport)
        except ValueError:
            pass
    if discovery_info.port:
        return int(discovery_info.port)
    return DEFAULT_LOCAL_API_PORT


def parse_manual_address(value: str) -> tuple[str, int]:
    """
    Split a manually configured address into host and local API port.

    Accepts ``192.168.1.10``, ``192.168.1.10:1444``, ``[2001:db8::1]:1444`` and a bare
    IPv6 address. Without an explicit port the default local API port is returned, so a
    zone of a multi-zone amplifier must be configured with its port.
    """
    value = value.strip()
    # a bare IPv6 address has more than one colon and no brackets
    if value.count(":") > 1 and not value.startswith("["):
        return value, DEFAULT_LOCAL_API_PORT
    try:
        parts = urlsplit(f"//{value}")
        host = parts.hostname
        port = parts.port
    except ValueError:
        return value, DEFAULT_LOCAL_API_PORT
    if not host:
        return value, DEFAULT_LOCAL_API_PORT
    return host, port or DEFAULT_LOCAL_API_PORT


def zone_index_from_player_id(player_id: str) -> int:
    """
    Return the zone index (0 for a single-zone speaker) encoded in a Sonos player id.

    The trailing digits of the id are the zone's UPnP port: 01400 for a single-zone
    speaker or the first zone, 01500 for the second zone and so on.
    """
    suffix = player_id.removeprefix("RINCON_")[12:]
    if not suffix.isdigit():
        return 0
    offset = int(suffix) - _BASE_UPNP_PORT
    if offset < 0 or offset % _ZONE_PORT_STEP:
        return 0
    index = offset // _ZONE_PORT_STEP
    return index if index < _MAX_ZONES else 0


def mac_address_from_player_id(player_id: str) -> str | None:
    """
    Derive the MAC address of a Sonos player from its player id.

    The 12 hex characters after ``RINCON_`` are the device's MAC address. For the second
    and later zones of a multi-zone product the last octet is incremented by the zone
    index, which matches the id its AirPlay endpoint advertises, so each zone links to
    its own AirPlay player instead of all zones claiming the amplifier's base address.
    """
    body = player_id.removeprefix("RINCON_")
    # 12 hex characters for the MAC address plus the 5 digit port suffix
    if len(body) < 17:
        return None
    mac_hex = body[:12]
    try:
        mac_int = int(mac_hex, 16)
    except ValueError:
        return None
    zone_index = zone_index_from_player_id(player_id)
    if zone_index and (mac_int & 0xFF) + zone_index <= 0xFF:
        mac_int += zone_index
    mac_hex = f"{mac_int:012X}"
    return ":".join(mac_hex[i : i + 2] for i in range(0, 12, 2))
