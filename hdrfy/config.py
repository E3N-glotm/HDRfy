"""Conversion settings and built-in inverse-tone-mapping presets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReconstructionPreset:
    """Parameters controlling the deterministic SDR-to-HDR reconstruction."""

    name: str
    highlight_start: float
    highlight_end: float
    curve_power: float
    detail_strength: float
    saturation_rolloff: float


PRESETS: dict[str, ReconstructionPreset] = {
    "conservative": ReconstructionPreset(
        name="conservative",
        highlight_start=0.62,
        highlight_end=0.98,
        curve_power=1.35,
        detail_strength=0.08,
        saturation_rolloff=0.08,
    ),
    "natural": ReconstructionPreset(
        name="natural",
        highlight_start=0.50,
        highlight_end=0.98,
        curve_power=1.15,
        detail_strength=0.14,
        saturation_rolloff=0.13,
    ),
    "vivid": ReconstructionPreset(
        name="vivid",
        highlight_start=0.38,
        highlight_end=0.96,
        curve_power=0.95,
        detail_strength=0.22,
        saturation_rolloff=0.18,
    ),
}


@dataclass(frozen=True, slots=True)
class ConversionConfig:
    """Complete conversion configuration.

    HDR values are relative to ``reference_white_nits``. A linear value of 1.0
    represents SDR reference white, while ``peak_nits / reference_white_nits``
    determines the maximum content boost encoded in the gain map.
    """

    preset: str = "natural"
    peak_nits: float = 1000.0
    reference_white_nits: float = 203.0
    base_quality: int = 95
    gainmap_quality: int = 95
    gainmap_scale: int = 2
    multi_channel_gainmap: bool = True
    pad_to_even: bool = False
    preserve_exif: bool = True
    force_sdr_heif: bool = False

    def validate(self) -> None:
        if self.preset not in PRESETS:
            choices = ", ".join(sorted(PRESETS))
            raise ValueError(f"Unknown preset {self.preset!r}; expected one of: {choices}")
        if not 203.0 <= self.peak_nits <= 10000.0:
            raise ValueError("peak_nits must be in the Ultra HDR range [203, 10000]")
        if self.reference_white_nits <= 0:
            raise ValueError("reference_white_nits must be positive")
        if self.peak_nits < self.reference_white_nits:
            raise ValueError("peak_nits must not be below reference_white_nits")
        if not 0 <= self.base_quality <= 100:
            raise ValueError("base_quality must be in [0, 100]")
        if not 0 <= self.gainmap_quality <= 100:
            raise ValueError("gainmap_quality must be in [0, 100]")
        if not 1 <= self.gainmap_scale <= 128:
            raise ValueError("gainmap_scale must be in [1, 128]")

    @property
    def max_content_boost(self) -> float:
        return self.peak_nits / self.reference_white_nits

    @property
    def reconstruction_preset(self) -> ReconstructionPreset:
        return PRESETS[self.preset]
