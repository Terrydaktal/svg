#!/usr/bin/env python3
import argparse
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

import vtracer

RGB_RE = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)

def parse_hex6(s: str):
    s = s.strip().lower()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6:
        raise ValueError("Expected 6-digit hex like FF00FF")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))

def color_to_rgb(v: str):
    if not v:
        return None
    v = v.strip().lower()
    if v == "none":
        return None
    if v.startswith("#") and len(v) == 7:
        return (int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16))
    m = RGB_RE.fullmatch(v)
    if m:
        return tuple(int(x) for x in m.groups())
    return None

def rgb_dist(a, b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)

def style_get(style: str, key: str):
    if not style:
        return None
    for part in style.split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        if k.strip().lower() == key.lower():
            return v.strip()
    return None

def style_set(style: str, key: str, value: str):
    parts = []
    found = False
    for part in [p.strip() for p in style.split(";") if p.strip()]:
        if ":" not in part:
            parts.append(part)
            continue
        k, v = part.split(":", 1)
        if k.strip().lower() == key.lower():
            parts.append(f"{k.strip()}:{value}")
            found = True
        else:
            parts.append(f"{k.strip()}:{v.strip()}")
    if not found:
        parts.append(f"{key}:{value}")
    return ";".join(parts)

def remove_key_color_from_svg(svg_text: str, bg_rgb, tol: float):
    """
    Remove key-colored background shapes and key-colored stroke halos.
    With the hard-alpha preprocessing below, tol can stay low.
    """
    root = ET.fromstring(svg_text)
    parent = {c: p for p in root.iter() for c in p}

    removed = 0
    strokestripped = 0

    for el in list(root.iter()):
        style = el.get("style", "")

        fill_v = el.get("fill") or style_get(style, "fill")
        stroke_v = el.get("stroke") or style_get(style, "stroke")

        fill_rgb = color_to_rgb(fill_v) if fill_v else None
        stroke_rgb = color_to_rgb(stroke_v) if stroke_v else None

        fill_is_bg = fill_rgb and rgb_dist(fill_rgb, bg_rgb) <= tol
        stroke_is_bg = stroke_rgb and rgb_dist(stroke_rgb, bg_rgb) <= tol

        # Remove pure background elements
        if fill_is_bg and (not stroke_rgb or stroke_is_bg):
            p = parent.get(el)
            if p is not None:
                p.remove(el)
                removed += 1
            continue

        # Strip bg-colored strokes (rare after hard-alpha flattening)
        if stroke_is_bg:
            if "stroke" in el.attrib:
                el.set("stroke", "none")
            else:
                el.set("style", style_set(style, "stroke", "none"))
            strokestripped += 1

    return ET.tostring(root, encoding="unicode"), removed, strokestripped

def preprocess_flat_keyed_rgb(
    in_png: str,
    out_png_rgb: str,
    *,
    white_rgb,
    blue_rgb,
    bg_rgb,
    alpha_cutoff: int,
    scale: int,
    blue_dom_delta: int,
    blue_min_b: int,
    white_min: int,
):
    """
    Turn the input into a 'traceable' RGB image:
      - binary alpha (alpha <= cutoff => background)
      - foreground pixels mapped to EXACTLY {white_rgb, blue_rgb}
      - background pixels set to bg_rgb (key color)
      - output is opaque RGB (no alpha), preventing halo creation

    The classification is intentionally simple/robust:
      - blue if B channel dominates and is sufficiently strong
      - else white
    """

    img = Image.open(in_png).convert("RGBA")
    if scale != 1:
        img = img.resize((img.size[0]*scale, img.size[1]*scale), Image.Resampling.LANCZOS)

    arr = np.array(img, dtype=np.uint8)  # HxWx4
    rgb = arr[..., :3].astype(np.int16)
    a = arr[..., 3].astype(np.int16)

    # Foreground mask: hard alpha
    fg = a > int(alpha_cutoff)

    r = rgb[..., 0]
    g = rgb[..., 1]
    b = rgb[..., 2]

    # Blue detection (cursor): B dominates max(R,G) by blue_dom_delta, and B above blue_min_b
    max_rg = np.maximum(r, g)
    is_blue = fg & ((b - max_rg) >= int(blue_dom_delta)) & (b >= int(blue_min_b))

    # White detection (waveform + pointer): treat everything else foreground as white.
    # Optionally you can require a minimum whiteness for safety, but for your icon this is fine.
    is_white = fg & ~is_blue

    out = np.zeros((arr.shape[0], arr.shape[1], 3), dtype=np.uint8)
    out[:, :] = np.array(bg_rgb, dtype=np.uint8)

    out[is_white] = np.array(white_rgb, dtype=np.uint8)
    out[is_blue] = np.array(blue_rgb, dtype=np.uint8)

    Image.fromarray(out, mode="RGB").save(out_png_rgb)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_png")
    ap.add_argument("output_svg", nargs="?", default=None)

    # Palette (you can change these)
    ap.add_argument("--white", default="FFFFFF", help="Waveform/pointer fill (default FFFFFF)")
    ap.add_argument("--blue", default="276EE6", help="Cursor fill (default 276EE6)")
    ap.add_argument("--bghex", default="FF00FF", help="Key background color (default FF00FF)")

    # Flattening controls (this is the “do it first” part)
    ap.add_argument("--alpha-cutoff", type=int, default=12,
                    help="Alpha <= cutoff becomes background (hard edge). Default 12.")
    ap.add_argument("--scale", type=int, default=6,
                    help="Upscale before flatten+trace to smooth curves. Default 6.")

    # Blue classifier knobs (tune if cursor misclassifies)
    ap.add_argument("--blue-dom-delta", type=int, default=25,
                    help="Blue if B - max(R,G) >= delta. Default 25.")
    ap.add_argument("--blue-min-b", type=int, default=80,
                    help="Blue if B >= this. Default 80.")

    # SVG bg removal
    ap.add_argument("--bg-tol", type=float, default=5.0,
                    help="Tolerance for removing bg color from SVG. With flat fills, keep low. Default 5.")
    ap.add_argument("--save-flat", action="store_true",
                    help="Also write a debug flat RGB PNG next to the SVG ('.flat.png').")

    # VTracer params (keep moderate; flat input = clean output)
    ap.add_argument("--hierarchical", default="cutout", choices=["stacked", "cutout"])
    ap.add_argument("--mode", default="spline", choices=["spline", "polygon", "none"])
    ap.add_argument("--filter-speckle", type=int, default=16)
    ap.add_argument("--corner-threshold", type=int, default=80)
    ap.add_argument("--length-threshold", type=float, default=10.0)
    ap.add_argument("--max-iterations", type=int, default=10)
    ap.add_argument("--splice-threshold", type=int, default=45)
    ap.add_argument("--path-precision", type=int, default=2)

    args = ap.parse_args()

    inp = args.input_png
    out = args.output_svg or (os.path.splitext(inp)[0] + ".svg")

    white_rgb = parse_hex6(args.white)
    blue_rgb  = parse_hex6(args.blue)
    bg_rgb    = parse_hex6(args.bghex)

    with tempfile.TemporaryDirectory() as td:
        keyed_rgb_png = os.path.join(td, "keyed_rgb.png")
        raw_svg = os.path.join(td, "raw.svg")

        preprocess_flat_keyed_rgb(
            inp,
            keyed_rgb_png,
            white_rgb=white_rgb,
            blue_rgb=blue_rgb,
            bg_rgb=bg_rgb,
            alpha_cutoff=args.alpha_cutoff,
            scale=args.scale,
            blue_dom_delta=args.blue_dom_delta,
            blue_min_b=args.blue_min_b,
            white_min=200,
        )

        if args.save_flat:
            debug_flat = os.path.splitext(out)[0] + ".flat.png"
            Image.open(keyed_rgb_png).save(debug_flat)
            print(f"Wrote debug flat PNG: {debug_flat}")

        vtracer.convert_image_to_svg_py(
            keyed_rgb_png, raw_svg,
            colormode="color",
            hierarchical=args.hierarchical,
            mode=args.mode,
            filter_speckle=args.filter_speckle,
            corner_threshold=args.corner_threshold,
            length_threshold=args.length_threshold,
            max_iterations=args.max_iterations,
            splice_threshold=args.splice_threshold,
            path_precision=args.path_precision,
            # These two matter less now because we’ve already forced a 3-color image:
            color_precision=8,
            layer_difference=64,
        )

        svg_text = open(raw_svg, "r", encoding="utf-8", errors="replace").read()
        cleaned, removed, stripped = remove_key_color_from_svg(svg_text, bg_rgb, args.bg_tol)

        with open(out, "w", encoding="utf-8") as f:
            f.write(cleaned)

    print(f"Wrote: {out} (removed {removed} bg elems, stripped {stripped} bg strokes)")

if __name__ == "__main__":
    main()
