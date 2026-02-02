#!/usr/bin/env python3
import argparse
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

from PIL import Image

import vtracer


def hex6(s: str) -> str:
    s = s.strip().lower()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6 or any(c not in "0123456789abcdef" for c in s):
        raise ValueError(f"Expected 6-digit hex like FF00FF, got: {s!r}")
    return s


_RGB_RE = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)
_FILL_RE = re.compile(r"fill\s*:\s*([^;]+)", re.I)


def normalize_color(value: str) -> str | None:
    """
    Normalize color strings to '#rrggbb' when possible.
    Handles '#RRGGBB' and 'rgb(r,g,b)'. Returns None if unknown.
    """
    if not value:
        return None
    v = value.strip().lower()
    if v.startswith("#") and len(v) == 7:
        return v
    m = _RGB_RE.fullmatch(v)
    if m:
        r, g, b = (max(0, min(255, int(x))) for x in m.groups())
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


def element_fill_color(el: ET.Element) -> str | None:
    # direct fill=""
    fill = el.get("fill")
    nf = normalize_color(fill) if fill else None
    if nf:
        return nf

    # style="...fill:...;"
    style = el.get("style", "")
    m = _FILL_RE.search(style)
    if m:
        return normalize_color(m.group(1))
    return None


def strip_background(svg_in: str, bg_hex6: str) -> str:
    """
    Remove elements whose fill matches the background key color.
    """
    target = f"#{bg_hex6.lower()}"

    # keep namespace if present
    ET.register_namespace("", "http://www.w3.org/2000/svg")

    root = ET.fromstring(svg_in)
    parent = {c: p for p in root.iter() for c in p}

    removed = 0
    for el in list(root.iter()):
        c = element_fill_color(el)
        if c == target:
            p = parent.get(el)
            if p is not None:
                p.remove(el)
                removed += 1

    # If we removed everything (rare), fall back to original.
    if removed == 0:
        return svg_in

    return ET.tostring(root, encoding="unicode")


def preprocess_png_to_key_bg(inp_png: str, out_png: str, bg_hex6: str, alpha_threshold: int) -> None:
    """
    Replace transparent pixels (alpha <= threshold) with a solid key background color.
    This makes it easy to trace and then delete background shapes from the SVG.
    """
    bg_hex6 = bg_hex6.lower()
    bg_rgb = tuple(int(bg_hex6[i:i+2], 16) for i in (0, 2, 4))

    img = Image.open(inp_png).convert("RGBA")
    px = img.load()

    w, h = img.size
    thr = max(0, min(255, alpha_threshold))

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a <= thr:
                px[x, y] = (*bg_rgb, 255)

    img.save(out_png)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Full-color PNG->SVG vectorization using VTracer, with optional background-key removal."
    )
    ap.add_argument("input_png", help="Input PNG path")
    ap.add_argument("output_svg", nargs="?", help="Output SVG path (default: input name with .svg)")
    ap.add_argument("--bghex", default="FF00FF", help="Key background color hex (default: FF00FF)")
    ap.add_argument("--alpha-threshold", type=int, default=2,
                    help="Pixels with alpha <= this are treated as background (default: 2)")
    ap.add_argument("--keep-bg", action="store_true",
                    help="Do not remove background color shapes from the SVG")

    # VTracer knobs (sane defaults for icons)
    ap.add_argument("--colormode", default="color", choices=["color", "binary"])
    ap.add_argument("--hierarchical", default="stacked", choices=["stacked", "cutout"])
    ap.add_argument("--mode", default="spline", choices=["spline", "polygon", "none"])
    ap.add_argument("--filter-speckle", type=int, default=4)
    ap.add_argument("--color-precision", type=int, default=6)
    ap.add_argument("--layer-difference", type=int, default=16)
    ap.add_argument("--corner-threshold", type=int, default=60)
    ap.add_argument("--length-threshold", type=float, default=4.0)
    ap.add_argument("--max-iterations", type=int, default=10)
    ap.add_argument("--splice-threshold", type=int, default=45)
    ap.add_argument("--path-precision", type=int, default=3,
                    help="Decimal places in SVG paths (lower = smaller file).")

    args = ap.parse_args()

    inp = args.input_png
    if not os.path.isfile(inp):
        ap.error(f"Input not found: {inp}")

    out = args.output_svg or (os.path.splitext(inp)[0] + ".svg")
    bg = hex6(args.bghex)

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        keyed_png = os.path.join(td, "keyed.png")
        raw_svg = os.path.join(td, "raw.svg")

        preprocess_png_to_key_bg(inp, keyed_png, bg, args.alpha_threshold)

        # Vectorize (full color)
        vtracer.convert_image_to_svg_py(
            keyed_png,
            raw_svg,
            colormode=args.colormode,
            hierarchical=args.hierarchical,
            mode=args.mode,
            filter_speckle=args.filter_speckle,
            color_precision=args.color_precision,
            layer_difference=args.layer_difference,
            corner_threshold=args.corner_threshold,
            length_threshold=args.length_threshold,
            max_iterations=args.max_iterations,
            splice_threshold=args.splice_threshold,
            path_precision=args.path_precision,
        )

        svg_text = open(raw_svg, "r", encoding="utf-8", errors="replace").read()

        if not args.keep_bg:
            svg_text = strip_background(svg_text, bg)

        with open(out, "w", encoding="utf-8") as f:
            f.write(svg_text)

    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
