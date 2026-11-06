import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

import av
import numpy as np
import zxingcpp


# QFT protocol constants
MAGIC = b"QFT1"
VERSION = 1

PACKET_MANIFEST = 0x01
PACKET_DATA = 0x02
PACKET_END = 0x03

# 28-byte big-endian packet header
HEADER_FORMAT = ">4sBBQIIHI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


def decode_packet(raw):
    """
    Decode a QFT binary packet.

    Returns:
        dict containing packet metadata and payload,
        or None if invalid.
    """

    if len(raw) < HEADER_SIZE:
        return None

    try:
        (
            magic,
            version,
            packet_type,
            transfer_id,
            sequence,
            total_chunks,
            payload_length,
            checksum,
        ) = struct.unpack(HEADER_FORMAT, raw[:HEADER_SIZE])

        if magic != MAGIC:
            return None

        if version != VERSION:
            return None

        if len(raw) != HEADER_SIZE + payload_length:
            return None

        payload = raw[HEADER_SIZE:]

        # CRC32 covers the first 24 header bytes + payload.
        calculated_crc = zlib.crc32(
            raw[:24] + payload
        ) & 0xFFFFFFFF

        if calculated_crc != checksum:
            return None

        return {
            "type": packet_type,
            "transfer_id": transfer_id,
            "sequence": sequence,
            "total_chunks": total_chunks,
            "payload": payload,
        }

    except (ValueError, struct.error):
        return None


def decode_manifest(payload):
    """
    Decode the JSON manifest.
    """

    try:
        manifest = json.loads(payload.decode("utf-8"))

        required = [
            "protocol",
            "version",
            "transfer_id",
            "filename",
            "file_size",
            "chunk_size",
            "total_chunks",
            "sha256",
        ]

        for field in required:
            if field not in manifest:
                raise ValueError(f"Missing manifest field: {field}")

        if manifest["protocol"] != "QFT":
            raise ValueError("Unsupported protocol")

        if manifest["version"] != VERSION:
            raise ValueError("Unsupported manifest version")

        if not isinstance(manifest["filename"], str):
            raise ValueError("Invalid filename")

        if not manifest["filename"] or Path(manifest["filename"]).name != manifest["filename"]:
            raise ValueError("Invalid filename in manifest")

        if not isinstance(manifest["file_size"], int) or manifest["file_size"] < 0:
            raise ValueError("Invalid file size")

        if not isinstance(manifest["chunk_size"], int) or manifest["chunk_size"] <= 0:
            raise ValueError("Invalid chunk size")

        if not isinstance(manifest["total_chunks"], int) or manifest["total_chunks"] < 0:
            raise ValueError("Invalid total chunks")

        expected_chunks = (
            (manifest["file_size"] + manifest["chunk_size"] - 1)
            // manifest["chunk_size"]
        )

        if manifest["total_chunks"] != expected_chunks:
            raise ValueError("Manifest chunk count does not match file size")

        transfer_id = bytes.fromhex(manifest["transfer_id"])

        if len(transfer_id) != 8:
            raise ValueError("Invalid transfer ID length")

        if not isinstance(manifest["sha256"], str) or len(manifest["sha256"]) != 64:
            raise ValueError("Invalid SHA-256")

        return manifest

    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as e:
        raise ValueError(f"Invalid manifest: {e}")


def scan_video(video_path, output_dir):
    video_path = Path(video_path)
    output_dir = Path(output_dir)

    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = None
    transfer_id = None

    received = {}
    invalid_sequences = set()
    conflicting_sequences = set()

    seen_qr = set()

    total_frames = 0
    decoded_frames = 0
    qr_count = 0

    print(f"Opening video: {video_path}")

    container = av.open(str(video_path))

    try:
        for frame in container.decode(video=0):
            total_frames += 1

            # Convert frame to grayscale.
            image = frame.to_ndarray(format="gray")

            # Decode QR codes.
            results = zxingcpp.read_barcodes(image)

            for result in results:
                if not result.valid:
                    continue

                # IMPORTANT:
                # Use raw bytes, not result.text.
                raw = result.bytes

                if not raw:
                    continue

                # Avoid repeatedly processing identical QR codes.
                qr_hash = hashlib.sha256(raw).digest()

                if qr_hash in seen_qr:
                    continue

                seen_qr.add(qr_hash)
                qr_count += 1

                decoded_frames += 1

                # -----------------------------------------
                # Manifest packet
                # -----------------------------------------

                packet = decode_packet(raw)

                if packet is None:
                    # Also accept a plain JSON manifest QR.
                    # This is useful if the sender emits the
                    # manifest without a binary QFT header.
                    if manifest is None:
                        try:
                            candidate = json.loads(raw.decode("utf-8"))

                            if candidate.get("protocol") == "QFT":
                                manifest = decode_manifest(raw)
                                transfer_id = bytes.fromhex(
                                    manifest["transfer_id"]
                                )

                                print("\nManifest received:")
                                print(json.dumps(
                                    manifest,
                                    indent=4
                                ))

                                (output_dir / "manifest.json").write_text(
                                    json.dumps(manifest, indent=4),
                                    encoding="utf-8"
                                )

                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                            pass

                    continue

                # -----------------------------------------
                # Validate transfer ID
                # -----------------------------------------

                if manifest is None:

                    if packet["type"] == PACKET_MANIFEST:
                        try:
                            manifest = decode_manifest(
                                packet["payload"]
                            )

                            transfer_id = bytes.fromhex(
                                manifest["transfer_id"]
                            )

                            if packet["transfer_id"] != int.from_bytes(
                                transfer_id, "big"
                            ):
                                raise ValueError(
                                    "Manifest packet transfer ID mismatch"
                                )

                            if packet["total_chunks"] != manifest["total_chunks"]:
                                raise ValueError(
                                    "Manifest packet chunk count mismatch"
                                )

                            print("\nManifest received:")
                            print(json.dumps(
                                manifest,
                                indent=4
                            ))

                            (output_dir / "manifest.json").write_text(
                                json.dumps(manifest, indent=4),
                                encoding="utf-8"
                            )

                        except ValueError as e:
                            print(f"\nInvalid manifest: {e}")

                    continue

                if packet["transfer_id"] != int.from_bytes(
                    transfer_id, "big"
                ):
                    continue

                if packet["total_chunks"] != manifest["total_chunks"]:
                    continue

                # -----------------------------------------
                # Data packet
                # -----------------------------------------

                if packet["type"] != PACKET_DATA:
                    continue

                sequence = packet["sequence"]
                payload = packet["payload"]

                if not 0 <= sequence < manifest["total_chunks"]:
                    print(f"Invalid sequence number: {sequence}")
                    continue

                # Check expected chunk length.
                expected_length = min(
                    manifest["chunk_size"],
                    manifest["file_size"]
                    - sequence * manifest["chunk_size"]
                )

                if len(payload) != expected_length:
                    print(
                        f"Invalid chunk {sequence}: "
                        f"expected {expected_length} bytes, "
                        f"received {len(payload)}"
                    )

                    invalid_sequences.add(sequence)
                    continue

                # -----------------------------------------
                # Handle duplicate chunks
                # -----------------------------------------

                if sequence in received:

                    if received[sequence] == payload:
                        continue

                    print(f"Conflicting duplicate chunk: {sequence}")

                    conflicting_sequences.add(sequence)
                    invalid_sequences.add(sequence)
                    received.pop(sequence, None)

                    chunk_path = output_dir / str(sequence)

                    if chunk_path.exists():
                        chunk_path.unlink()

                    continue

                if sequence in conflicting_sequences:
                    continue

                # -----------------------------------------
                # Save chunk
                # -----------------------------------------

                chunk_path = output_dir / str(sequence)

                chunk_path.write_bytes(payload)

                received[sequence] = payload
                invalid_sequences.discard(sequence)

                print(
                    f"\rReceived chunk "
                    f"{sequence + 1}/{manifest['total_chunks']}",
                    end="",
                    flush=True
                )

    finally:
        container.close()

    print("\n\nVideo scanning completed.")

    # -----------------------------------------
    # Report results
    # -----------------------------------------

    if manifest is None:
        print("ERROR: No valid manifest found.")
        return

    total_chunks = manifest["total_chunks"]

    missing = sorted(
        set(range(total_chunks)) - set(received.keys())
    )

    print("\n========== TRANSFER REPORT ==========")

    print(f"Filename:       {manifest['filename']}")
    print(f"File size:      {manifest['file_size']:,} bytes")
    print(f"Chunk size:     {manifest['chunk_size']:,} bytes")
    print(f"Total chunks:   {total_chunks}")
    print(f"Received:       {len(received)}")
    print(f"Missing:        {len(missing)}")
    print(f"Invalid:        {len(invalid_sequences)}")
    print(f"QR codes read:  {qr_count}")
    print(f"Frames scanned: {total_frames}")

    if missing:
        print("\nMissing chunks:")
        print(missing)

    if invalid_sequences:
        print("\nInvalid chunks:")
        print(sorted(invalid_sequences))

    if conflicting_sequences:
        print("\nConflicting chunks:")
        print(sorted(conflicting_sequences))

    # -----------------------------------------
    # Reconstruct and verify
    # -----------------------------------------

    if not missing and not invalid_sequences:

        reconstructed = output_dir / manifest["filename"]

        hasher = hashlib.sha256()

        with reconstructed.open("wb") as output:

            for sequence in range(total_chunks):
                chunk_path = output_dir / str(sequence)

                if not chunk_path.is_file():
                    raise RuntimeError(
                        f"Chunk file unexpectedly missing: {sequence}"
                    )

                chunk = chunk_path.read_bytes()

                output.write(chunk)
                hasher.update(chunk)

        actual_hash = hasher.hexdigest()

        if actual_hash == manifest["sha256"]:

            print("\nSUCCESS!")
            print("All chunks received.")
            print("SHA-256 verified.")
            print(f"Reconstructed file: {reconstructed}")

        else:

            print("\nERROR: SHA-256 mismatch!")
            print(f"Expected: {manifest['sha256']}")
            print(f"Actual:   {actual_hash}")

            reconstructed.unlink()

    else:
        print("\nINCOMPLETE TRANSFER")
        print("Valid chunks have been preserved.")
        print("You can scan another video into the same folder later.")

    print(f"\nChunk folder: {output_dir}")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="QFT QR Video Receiver"
    )

    parser.add_argument(
        "video",
        help="Path to the iPhone video"
    )

    parser.add_argument(
        "output",
        help="Folder to save received chunks"
    )

    args = parser.parse_args()

    scan_video(args.video, args.output)
