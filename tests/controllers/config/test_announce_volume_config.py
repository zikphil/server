"""
Tests for the per-player announcement-volume strategy default.

A player whose native announcement route ignores a requested level (it drops
AnnouncementFeature.SUPPORTS_VOLUME) defaults to no volume adjustment, so it
never gets an untunable volume bump. Every other player keeps the "percentual"
default.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from music_assistant_models.enums import PlayerFeature, PlayerType, ProviderType

from music_assistant.constants import CONF_ANNOUNCE_VOLUME_STRATEGY
from music_assistant.mass import MusicAssistant
from music_assistant.models.player import AnnouncementFeature, DeviceInfo, Player

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ConfigEntry


class _TestProvider:
    """Minimal PlayerProvider stand-in backed by the real MusicAssistant."""

    def __init__(self, mass: MusicAssistant, domain: str) -> None:
        """Initialize the test provider."""
        self.mass = mass
        self.domain = domain
        self.instance_id = domain
        self.translation_owner = f"provider.{domain}"
        self.name = f"{domain.title()} Provider"
        self.available = True
        self.logger = logging.getLogger(f"test.{domain}")
        self.manifest = MagicMock()
        self.manifest.domain = domain
        self.manifest.name = self.name
        self.manifest.type = ProviderType.PLAYER
        self.type = ProviderType.PLAYER
        self.players: list[Player] = []

    async def unload(self, is_removed: bool = False) -> None:
        """Unload the provider (nothing to clean up)."""


class _NativeVolumePlayer(Player):
    """Player whose native announcement route applies a requested level."""

    def __init__(self, provider: _TestProvider, player_id: str) -> None:
        """Initialize the test player."""
        super().__init__(provider, player_id)  # type: ignore[arg-type]
        self._attr_name = player_id
        self._attr_type = PlayerType.PLAYER
        self._attr_available = True
        self._attr_powered = True
        self._attr_supported_features = {
            PlayerFeature.VOLUME_SET,
            PlayerFeature.PLAY_ANNOUNCEMENT,
        }
        self._attr_device_info = DeviceInfo(model="Test Model", manufacturer="Test Manufacturer")
        self._cache.clear()
        self.update_state(signal_event=False)

    async def stop(self) -> None:
        """Stop playback - required abstract method."""


class _NoNativeVolumePlayer(_NativeVolumePlayer):
    """Player whose native announcement route ignores a requested level."""

    @property
    def announcement_features(self) -> set[AnnouncementFeature]:
        """Report no announcement capabilities: the native route ignores the level."""
        return set()


def _register(mass: MusicAssistant, player: Player) -> None:
    """Register the player and its provider on the real MusicAssistant."""
    provider = player.provider
    mass._providers[provider.instance_id] = provider
    mass._provider_manifests[provider.domain] = provider.manifest
    mass.players._players[player.player_id] = player


def _strategy_default(entries: list[ConfigEntry]) -> object:
    """Return the resolved default of the announce-volume strategy entry."""
    entry = next(entry for entry in entries if entry.key == CONF_ANNOUNCE_VOLUME_STRATEGY)
    return entry.default_value


async def test_native_volume_player_defaults_to_percentual(mass: MusicAssistant) -> None:
    """A player whose native announcement honours the level keeps the percentual default."""
    provider = _TestProvider(mass, "native_vol")
    player = _NativeVolumePlayer(provider, "native_1")
    _register(mass, player)

    entries = await mass.config.get_player_config_entries("native_1")

    assert _strategy_default(entries) == "percentual"


async def test_no_native_volume_player_defaults_to_none(mass: MusicAssistant) -> None:
    """A player whose native announcement ignores the level defaults to no adjustment."""
    provider = _TestProvider(mass, "no_native_vol")
    player = _NoNativeVolumePlayer(provider, "no_native_1")
    _register(mass, player)

    entries = await mass.config.get_player_config_entries("no_native_1")

    assert _strategy_default(entries) == "none"
