"""Project-specific exceptions."""


class HDRfyError(RuntimeError):
    """Base exception for expected HDRfy failures."""


class UnsupportedInputError(HDRfyError):
    """Raised when an input file cannot be decoded safely."""


class ExistingHDRInputError(HDRfyError):
    """Raised when an HDR HEIF/AVIF is passed to the SDR reconstruction path."""


class UltraHDREncodeError(HDRfyError):
    """Raised when gain-map generation or Ultra HDR JPEG packaging fails."""
