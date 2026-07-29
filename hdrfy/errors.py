"""Project-specific exceptions."""


class HDRfyError(RuntimeError):
    """Base exception for expected HDRfy failures."""


class UnsupportedInputError(HDRfyError):
    """Raised when an input file cannot be decoded safely."""


class ExistingHDRInputError(HDRfyError):
    """Raised when an HDR HEIF/AVIF is passed to the SDR reconstruction path."""


class UltraHDREncoderNotFound(HDRfyError):
    """Raised when the libultrahdr demo encoder executable cannot be located."""


class UltraHDREncodeError(HDRfyError):
    """Raised when libultrahdr rejects the generated raw intents."""
