# QR File Sender (Windows + macOS)

A cross-platform Python sender that splits a file into binary packets, encodes each packet as a QR code, and displays the codes sequentially in a full-screen window. It uses Tkinter for the UI, so the same sender works on Windows and macOS.

## Requirements

- Windows 10/11 or macOS
- Python 3.10+
- Tkinter. On Windows, install Python from https://www.python.org/downloads/ and ensure the **tcl/tk and IDLE** feature is selected in the installer. Tkinter is normally included.

## Windows setup (PowerShell)

Extract the ZIP, open PowerShell in the `qr_file_sender` folder, then run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, you can skip activation and use the virtual-environment Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe sender.py
```

## Windows setup (Command Prompt)

```bat
py -3 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run

Open a file picker:

```powershell
python sender.py
```

Or specify a file (quote paths containing spaces):

```powershell
python sender.py "C:\Users\YourName\Desktop\example.zip"
```

Tune the display:

```powershell
python sender.py "C:\Users\YourName\Desktop\example.zip" --chunk-size 512 --duration 1.0 --error-correction M
```

On macOS, use the same commands after activating the virtual environment with `source .venv/bin/activate`.

## Options

- `--chunk-size`: bytes of file data per QR packet (default 1024; prototype allows 1–4000)
- `--duration`: seconds to display each QR code (default 0.8)
- `--error-correction`: QR error correction L/M/Q/H (default M)

## Full-screen controls

- `Space`: pause / resume
- `Left` / `Right`: step backward / forward (stepping pauses playback)
- `Escape`: exit

The QR code includes the required quiet zone. Keep the screen brightness high, avoid reflections, and record the display square-on where possible. On Windows, if full-screen mode does not engage correctly, update Python and verify Tkinter is installed.

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

Data payloads are raw binary bytes (not Base64). CRC32 validates each packet payload. SHA-256 validates the fully reconstructed file on the receiver.

## Prototype limitations

- No automatic retry or feedback channel.
- The sender transmits each QR code once. The receiver should identify missing sequence numbers by comparing received chunks against `0..total_chunks-1`.
- The receiver must reject packets with invalid magic/version/length/CRC and deduplicate valid data packets by transfer ID and sequence number.
- This is a first protocol prototype. QR readability and optimal chunk size/display duration must be measured with the actual display and recording phone.
