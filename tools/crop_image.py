#!/usr/bin/env python3
"""Image crop utility for document records and certificates.

Usage:
    # Absolute pixel box (left, top, right, bottom):
    python3 tools/crop_image.py sources/input.jpg sources/output-crop.jpg --box 100 200 800 600

    # Relative coordinates (0.0 to 1.0):
    python3 tools/crop_image.py sources/input.jpg sources/output-crop.jpg --rel 0.1 0.2 0.8 0.6
"""
import sys
import argparse
from pathlib import Path
from PIL import Image

def main():
    parser = argparse.ArgumentParser(description="Crop an image file to extract records or signatures.")
    parser.add_argument("src", help="Path to input image")
    parser.add_argument("dst", help="Path to output cropped image")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--box", nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                       help="Pixel coordinates (left top right bottom)")
    group.add_argument("--rel", nargs=4, type=float, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                       help="Relative fractions of image dimensions 0.0 to 1.0")

    args = parser.parse_args()

    src_path = Path(args.src)
    if not src_path.exists():
        sys.exit(f"Error: Source image not found: {src_path}")

    with Image.open(src_path) as im:
        w, h = im.size
        if args.box:
            left, top, right, bottom = args.box
        else:
            r_l, r_t, r_r, r_b = args.rel
            left = int(r_l * w)
            top = int(r_t * h)
            right = int(r_r * w)
            bottom = int(r_b * h)

        # Bounds validation
        left = max(0, min(left, w - 1))
        top = max(0, min(top, h - 1))
        right = max(left + 1, min(right, w))
        bottom = max(top + 1, min(bottom, h))

        cropped = im.crop((left, top, right, bottom))
        dst_path = Path(args.dst)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(dst_path, quality=95)
        print(f"Saved crop: {dst_path} ({cropped.width}x{cropped.height} px, cropped from {w}x{h} px)")

if __name__ == "__main__":
    main()
