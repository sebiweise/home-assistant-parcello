"""Tests for the brand images shipped with the integration."""

from pathlib import Path
import struct

import pytest

BRAND_DIR = Path(__file__).parent.parent / "custom_components" / "parcello" / "brand"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_size(path: Path) -> tuple[int, int]:
    """Return width and height from the IHDR chunk of a PNG file."""
    header = path.read_bytes()[:24]
    assert header[:8] == PNG_SIGNATURE, f"{path.name} is not a PNG file"
    assert header[12:16] == b"IHDR", f"{path.name} has no IHDR chunk"
    return struct.unpack(">II", header[16:24])


def _brand_files(prefix: str) -> list[Path]:
    return sorted(
        path
        for path in BRAND_DIR.glob("*.png")
        if path.name.removeprefix("dark_").startswith(prefix)
    )


@pytest.mark.parametrize(
    "name", ["icon.png", "icon@2x.png", "dark_icon.png", "dark_icon@2x.png"]
)
def test_icon_is_present(name: str) -> None:
    """HACS validates the brand assets through brand/icon.png (plus hDPI)."""
    assert (BRAND_DIR / name).is_file()


@pytest.mark.parametrize("path", _brand_files("icon"), ids=lambda path: path.name)
def test_icon_size(path: Path) -> None:
    """Icons are square, 256x256 or 512x512 for the hDPI variant."""
    expected = 512 if "@2x" in path.name else 256
    assert _png_size(path) == (expected, expected)


@pytest.mark.parametrize("path", _brand_files("logo"), ids=lambda path: path.name)
def test_logo_size(path: Path) -> None:
    """The shortest side of a logo is 128-256 px, 256-512 px for hDPI."""
    low, high = (256, 512) if "@2x" in path.name else (128, 256)
    assert low <= min(_png_size(path)) <= high
