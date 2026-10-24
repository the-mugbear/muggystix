"""PNG and JPEG headers: the ONE reader of an image's type and size from its
bytes (2.530.0), shared by the report-template asset upload
(``template_asset_store``) and profile pictures (``profile_image_service``).

A reader returns ``(width, height)`` only when the bytes really are that
format (the PNG signature and a complete file ending with IEND; the JPEG
start-of-image marker and a frame header), else None — so the size check is
also the opening-bytes check.
"""
import struct
from typing import Optional, Tuple


def png_size(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    # A complete file ends with the IEND chunk (a truncated upload does not).
    if data[-8:-4] != b"IEND":
        return None
    return struct.unpack(">II", data[16:24])


# Start-of-frame markers carry the frame's size (not DHT C4, JPG C8, DAC CC).
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def jpeg_size(data: bytes) -> Optional[Tuple[int, int]]:
    if len(data) < 4 or data[:3] != b"\xff\xd8\xff":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if length < 2:
            return None
        if marker in _SOF:
            if i + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    return None


def image_size(data: bytes, content_type: str) -> Optional[Tuple[int, int]]:
    """The size of a PNG or JPEG whose bytes match ``content_type``, else None."""
    if content_type == "image/png":
        return png_size(data)
    if content_type == "image/jpeg":
        return jpeg_size(data)
    return None
