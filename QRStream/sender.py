#!/usr/bin/env python3
"""Full-screen QR file sender for macOS.

Protocol v1 packet layout (big-endian):
    magic           4 bytes  b"QFT1"
    version         1 byte   1
    packet_type     1 byte   1=manifest, 2=data
    transfer_id     8 bytes
    sequence        4 bytes  zero-based data chunk index (manifest uses 0xFFFFFFFF)
    total_chunks    4 bytes
    payload_length  2 bytes
    crc32           4 bytes  CRC32 of payload
    payload         N bytes

All packet bytes are encoded in QR byte mode. The manifest payload is UTF-8 JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import secrets
import struct
import sys
import time
import uuid
import zlib
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M, ERROR_CORRECT_Q, ERROR_CORRECT_H, ERROR_CORRECT_L
    from qrcode.util import QRData, MODE_8BIT_BYTE
    from PIL import Image, ImageTk, ImageDraw
except ImportError:
    print("Missing dependencies. Run: python3 -m pip install -r requirements.txt", file=sys.stderr)
    raise

MAGIC = b"QFT1"
VERSION = 1
PACKET_MANIFEST = 1
PACKET_DATA = 2
MANIFEST_SEQUENCE = 0xFFFFFFFF
HEADER = struct.Struct(">4sBB8sIIHI")
ERROR_LEVELS = {
    "L": ERROR_CORRECT_L,
    "M": ERROR_CORRECT_M,
    "Q": ERROR_CORRECT_Q,
    "H": ERROR_CORRECT_H,
}


def make_packet(packet_type: int, transfer_id: bytes, sequence: int,
                total_chunks: int, payload: bytes) -> bytes:
    if len(payload) > 65535:
        raise ValueError("Packet payload exceeds the protocol's 65535-byte length field.")
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    return HEADER.pack(
        MAGIC, VERSION, packet_type, transfer_id, sequence,
        total_chunks, len(payload), checksum
    ) + payload


def make_qr_image(packet: bytes, error_level: str, box_size: int = 8) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_LEVELS[error_level],
        box_size=box_size,
        border=4,  # QR quiet zone: keep this intact.
    )
    qr.add_data(QRData(packet, mode=MODE_8BIT_BYTE), optimize=0)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def load_file_packets(path: Path, chunk_size: int, error_level: str):
    file_bytes = path.read_bytes()
    total_chunks = max(1, math.ceil(len(file_bytes) / chunk_size))
    transfer_id = secrets.token_bytes(8)

    manifest = {
        "protocol": "QFT",
        "version": VERSION,
        "transfer_id": transfer_id.hex(),
        "filename": path.name,
        "file_size": len(file_bytes),
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
        "sha256": hashlib.sha256(file_bytes).hexdigest(),
    }
    manifest_payload = json.dumps(
        manifest, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")

    packets = [("Manifest", make_packet(
        PACKET_MANIFEST, transfer_id, MANIFEST_SEQUENCE,
        total_chunks, manifest_payload
    ))]

    for index in range(total_chunks):
        start = index * chunk_size
        chunk = file_bytes[start:start + chunk_size]
        packets.append((f"Chunk {index + 1}/{total_chunks}", make_packet(
            PACKET_DATA, transfer_id, index, total_chunks, chunk
        )))

    # The sender returns the source digest in its completion screen/log.
    return packets, manifest, file_bytes


class FullScreenSender:
    def __init__(self, root: tk.Tk, packets, manifest, duration: float,
                 error_level: str):
        self.root = root
        self.packets = packets
        self.manifest = manifest
        self.duration_ms = max(100, int(duration * 1000))
        self.error_level = error_level
        self.index = 0
        self.paused = False
        self.after_id = None
        self.photo = None
        self.started_at = None

        root.title("QR File Sender")
        root.configure(bg="black")
        root.attributes("-fullscreen", True)
        root.bind("<Escape>", self.stop)
        root.bind("<space>", self.toggle_pause)
        root.bind("<Right>", self.next_packet)
        root.bind("<Left>", self.previous_packet)
        root.protocol("WM_DELETE_WINDOW", self.stop)

        self.image_label = tk.Label(root, bg="black", bd=0)
        self.image_label.pack(fill="both", expand=True)
        self.status = tk.Label(
            root, text="", fg="white", bg="black",
            font=("Helvetica", 12), pady=8
        )
        self.status.pack(side="bottom", fill="x")

        root.update_idletasks()
        self.show_current()
        self.started_at = time.monotonic()
        self.schedule_next()

    def available_size(self):
        self.root.update_idletasks()
        width = max(200, self.root.winfo_screenwidth() - 100)
        height = max(200, self.root.winfo_screenheight()
                     - self.status.winfo_height() - 100)
        side = min(width, height)
        return side

    def show_current(self):
        if self.index >= len(self.packets):
            self.finish()
            return

        label, packet = self.packets[self.index]
        qr_image = make_qr_image(packet, self.error_level, box_size=8)

        # Nearest-neighbour scaling preserves the hard QR module edges.
        side = self.available_size()
        qr_image.thumbnail((side, side), Image.Resampling.NEAREST)
        self.photo = ImageTk.PhotoImage(qr_image)
        self.image_label.configure(image=self.photo)

        total_data = self.manifest["total_chunks"]
        if self.index == 0:
            progress = "Manifest"
        else:
            progress = f"Chunk {self.index}/{total_data}"
        pause_text = "  |  PAUSED" if self.paused else ""
        self.status.configure(
            text=f"{self.manifest['filename']}  |  {progress}  |  "
                 f"{self.index + 1}/{len(self.packets)} packets"
                 f"{pause_text}  |  Space: pause  ←/→: step  Esc: stop"
        )

    def schedule_next(self):
        if not self.paused and self.index < len(self.packets):
            self.after_id = self.root.after(self.duration_ms, self.advance)

    def advance(self):
        self.after_id = None
        if self.paused:
            return
        self.index += 1
        if self.index >= len(self.packets):
            self.finish()
        else:
            self.show_current()
            self.schedule_next()

    def toggle_pause(self, _event=None):
        self.paused = not self.paused
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.show_current()
        if not self.paused:
            self.schedule_next()

    def next_packet(self, _event=None):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.paused = True
        self.index = min(self.index + 1, len(self.packets) - 1)
        self.show_current()

    def previous_packet(self, _event=None):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.paused = True
        self.index = max(0, self.index - 1)
        self.show_current()

    def finish(self):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        self.image_label.configure(image="")
        self.photo = None
        elapsed = time.monotonic() - self.started_at if self.started_at else 0
        self.status.configure(
            text=f"TRANSMISSION COMPLETE  |  {self.manifest['filename']}  |  "
                 f"{self.manifest['total_chunks']} chunks  |  "
                 f"{elapsed:.1f}s  |  Esc: exit"
        )

    def stop(self, _event=None):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
        self.root.destroy()


def choose_file():
    picker = tk.Tk()
    picker.withdraw()
    path = filedialog.askopenfilename(title="Choose a file to transmit")
    picker.destroy()
    return Path(path) if path else None


def main():
    parser = argparse.ArgumentParser(description="Display a file as sequential full-screen QR codes.")
    parser.add_argument("file", nargs="?", help="File to transmit. If omitted, a file picker opens.")
    parser.add_argument("--chunk-size", type=int, default=1024,
                        help="Payload bytes per QR packet (default: 1024).")
    parser.add_argument("--duration", type=float, default=0.8,
                        help="Seconds to display each QR code (default: 0.8).")
    parser.add_argument("--error-correction", choices=["L", "M", "Q", "H"], default="M",
                        help="QR error correction level (default: M).")
    args = parser.parse_args()

    if args.chunk_size < 1 or args.chunk_size > 4000:
        parser.error("--chunk-size must be between 1 and 4000 bytes for this prototype.")
    if args.duration <= 0:
        parser.error("--duration must be greater than zero.")

    path = Path(args.file).expanduser() if args.file else choose_file()
    if not path:
        print("No file selected.")
        return
    if not path.is_file():
        parser.error(f"Not a file: {path}")

    packets, manifest, _ = load_file_packets(path, args.chunk_size, args.error_correction)
    estimated_seconds = len(packets) * args.duration

    print(f"File: {path}")
    print(f"Size: {manifest['file_size']} bytes")
    print(f"Transfer ID: {manifest['transfer_id']}")
    print(f"Chunks: {manifest['total_chunks']}")
    print(f"Estimated display time: {estimated_seconds:.1f} seconds")
    print(f"SHA-256: {manifest['sha256']}")
    print("Starting full-screen display. Press Space to pause, Esc to exit.")

    root = tk.Tk()
    FullScreenSender(root, packets, manifest, args.duration, args.error_correction)
    root.mainloop()


if __name__ == "__main__":
    main()
