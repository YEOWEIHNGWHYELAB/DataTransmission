import sys
import zxingcpp
from PIL import Image


def scan_qr(image_path):
    try:
        # Load image
        image = Image.open(image_path)

        # Decode QR codes
        # single image could have multiple QR in it
        results = zxingcpp.read_barcodes(image)

        if not results:
            print("No QR code detected.")
            return

        for i, result in enumerate(results):
            print(f"\nQR Code #{i + 1}")
            print(f"Format: {result.format}")
            print(f"Content: {result.text}")

    except FileNotFoundError:
        print(f"File not found: {image_path}")

    except Exception as e:
        print(f"Error scanning image: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python qr_scanner.py <image_path>")
        sys.exit(1)

    scan_qr(sys.argv[1])
