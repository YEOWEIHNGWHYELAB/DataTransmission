# QR File Sender (macOS prototype)

A Python sender that splits a file into binary packets, encodes each packet as a QR code, and displays the codes sequentially in a full-screen macOS window.

## Requirements

- macOS with Python 3.10+
- Tkinter (usually included with the python.org macOS installer; some Python distributions package it separately)

## Setup

```bash
cd qr_file_sender
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run

Open a file picker:

```bash
python sender.py
```

Or specify a file:

```bash
python sender.py ~/Desktop/example.zip
```

Tune the display:

```bash
python sender.py ~/Desktop/example.zip --chunk-size 512 --duration 1.0 --error-correction M
```

Options:
- `--chunk-size`: bytes of file data per QR packet (default 1024; prototype allows 1–4000)
- `--duration`: seconds per QR code (default 0.8)
- `--error-correction`: QR error correction L/M/Q/H (default M)

## Full-screen controls

- `Space`: pause / resume
- `Left` / `Right`: step backward / forward (stepping pauses playback)
- `Escape`: exit

The QR code includes the required quiet zone. Keep the screen brightness high, avoid reflections, and record the display square-on where possible.

## Protocol v1

Every QR code contains a binary packet:

| Field | Size |
|---|---:|
| Magic (`QFT1`) | 4 bytes |
| Version | 1 byte |
| Packet type | 1 byte |
| Transfer ID | 8 bytes |
| Sequence number | 4 bytes |
| Total chunks | 4 bytes |
| Payload length | 2 bytes |
| CRC32 of payload | 4 bytes |
| Payload | Variable |

Packet types:
- `1`: manifest (sequence number `0xFFFFFFFF`)
- `2`: file data (zero-based sequence number)

The manifest payload is compact UTF-8 JSON containing the filename, file size, chunk size, total chunk count, transfer ID, and SHA-256 of the original file.

Data payloads are raw binary bytes (not Base64). CRC32 validates each packet payload. SHA-256 will validate the fully reconstructed file on the receiver.

## Prototype limitations

- No automatic retry or feedback channel.
- The sender transmits each chunk once. The receiver should identify missing sequence numbers by comparing received chunks against `0..total_chunks-1`.
- The receiver must reject packets with invalid magic/version/length/CRC and deduplicate valid data packets by transfer ID and sequence number.
- This is a first protocol prototype. QR readability and optimal chunk size/display duration must be measured with the actual MacBook and recording phone.
