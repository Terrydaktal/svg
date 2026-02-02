#!/usr/bin/env python3
import argparse
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageFilter

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
    style = style or ""
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
    With hard mask + forced palette, tol can stay low (e.g. 3-8).
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

        # Strip bg-colored strokes (should be rare now)
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
    bg_dist: int,
    mask_blur: float,
    morph: int,
):
    """
    Produce an opaque RGB image ready for vectorization:
      1) Foreground mask = (alpha > alpha_cutoff) AND (color far enough from bg)
         This works even if the PNG is fully opaque (alpha=255 everywhere).
      2) Clean matte: blur -> threshold -> optional close (max then min)
      3) Foreground pixels snapped to nearest of {white_rgb, blue_rgb}
      4) Background set to bg_rgb key color
    """

    img = Image.open(in_png).convert("RGBA")
    if scale != 1:
        img = img.resize((img.size[0]*scale, img.size[1]*scale), Image.Resampling.LANCZOS)

    arr = np.array(img, dtype=np.uint8)
    rgb = arr[..., :3].astype(np.int32)   # int32 avoids overflow
    a   = arr[..., 3].astype(np.int32)

    bg = np.array(bg_rgb, dtype=np.int32)
    diff = rgb - bg
    dist2 = (diff[..., 0]*diff[..., 0] + diff[..., 1]*diff[..., 1] + diff[..., 2]*diff[..., 2])

    fg_alpha = a > int(alpha_cutoff)
    fg_key   = dist2 > int(bg_dist*bg_dist)

    fg = fg_alpha & fg_key

    # --- matte cleanup: blur -> threshold -> optional close ---
    mask = Image.fromarray((fg.astype(np.uint8) * 255), mode="L")

    if mask_blur and mask_blur > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=float(mask_blur)))

    mask = mask.point(lambda p: 255 if p >= 128 else 0)

    # Close to remove tiny gaps / single-pixel jaggies (3 or 5 typical)
    if morph and morph >= 3:
        mask = mask.filter(ImageFilter.MaxFilter(size=int(morph))).filter(
            ImageFilter.MinFilter(size=int(morph))
        )

    fg = (np.array(mask, dtype=np.uint8) > 0)

    # --- classify to nearest palette color (robust on anti-aliased edges) ---
    w = np.array(white_rgb, dtype=np.int32)
    b = np.array(blue_rgb,  dtype=np.int32)

    dw = ((rgb[..., 0]-w[0])**2 + (rgb[..., 1]-w[1])**2 + (rgb[..., 2]-w[2])**2)
    db = ((rgb[..., 0]-b[0])**2 + (rgb[..., 1]-b[1])**2 + (rgb[..., 2]-b[2])**2)

    is_blue  = fg & (db < dw)
    is_white = fg & ~is_blue

    out = np.empty((arr.shape[0], arr.shape[1], 3), dtype=np.uint8)
    out[:] = np.array(bg_rgb, dtype=np.uint8)
    out[is_white] = np.array(white_rgb, dtype=np.uint8)
    out[is_blue]  = np.array(blue_rgb,  dtype=np.uint8)

    Image.fromarray(out, mode="RGB").save(out_png_rgb)

def get_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_png")
    ap.add_argument("output_svg", nargs="?", default=None)

    # Palette
    ap.add_argument("--white", default="FFFFFF", help="Waveform/pointer fill (default FFFFFF)")
    ap.add_argument("--blue",  default="276EE6", help="Cursor fill (default 276EE6)")
    ap.add_argument("--bghex", default="FF00FF", help="Key background color (default FF00FF)")

    # Preprocess controls
    ap.add_argument("--alpha-cutoff", type=int, default=128,
                    help="Alpha <= cutoff becomes background. Default 128.")
    ap.add_argument("--scale", type=int, default=2,
                    help="Upscale before mask+trace. Default 2 (try 3 if still jaggy).")

    ap.add_argument("--bg-dist", type=int, default=35,
                    help="Background key distance in RGB units. Higher = tighter cut. Default 35.")
    ap.add_argument("--mask-blur", type=float, default=0.8,
                    help="Gaussian blur radius for matte smoothing (after scaling). Default 0.8.")
    ap.add_argument("--morph", type=int, default=3,
                    help="Morphological close filter size (0 disables). Typical: 3 or 5. Default 3.")

    # SVG bg removal
    ap.add_argument("--bg-tol", type=float, default=5.0,
                    help="Tolerance for removing bg color from SVG. Default 5.")
    ap.add_argument("--save-flat", action="store_true",
                    help="Also write a debug flat RGB PNG next to the SVG ('.flat.png').")

    # VTracer params
    ap.add_argument("--hierarchical", default="cutout", choices=["stacked", "cutout"])
    ap.add_argument("--mode", default="spline", choices=["spline", "polygon", "none"])
    ap.add_argument("--filter-speckle", type=int, default=16)
    ap.add_argument("--corner-threshold", type=int, default=80)
    ap.add_argument("--length-threshold", type=float, default=10.0)
    ap.add_argument("--max-iterations", type=int, default=10)
    ap.add_argument("--splice-threshold", type=int, default=45)
    ap.add_argument("--path-precision", type=int, default=2)

    return ap

def generate_svg(args):
    inp = args.input_png
    # If called from wrapper, we might not have output_svg set, but we need the basename for debug files
    # If input is a file path, use it.
    base_name = os.path.splitext(inp)[0]

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
            bg_dist=args.bg_dist,
            mask_blur=args.mask_blur,
            morph=args.morph,
        )

        if args.save_flat:
            # save next to input or output if possible
            out_target = args.output_svg if args.output_svg else (base_name + ".svg")
            debug_flat = os.path.splitext(out_target)[0] + ".flat.png"
            Image.open(keyed_rgb_png).save(debug_flat)
            print(f"Wrote debug flat PNG: {debug_flat}", file=sys.stderr)

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
            # Already forced palette, these matter less now:
            color_precision=8,
            layer_difference=64,
        )

        svg_text = open(raw_svg, "r", encoding="utf-8", errors="replace").read()
        cleaned, removed, stripped = remove_key_color_from_svg(svg_text, bg_rgb, args.bg_tol)
        
        # We return the cleaned SVG string and some metadata stats
        return cleaned, removed, stripped

def main():
    ap = get_parser()
    args = ap.parse_args()
    
    out = args.output_svg or (os.path.splitext(args.input_png)[0] + ".svg")
    
    cleaned, removed, stripped = generate_svg(args)

    with open(out, "w", encoding="utf-8") as f:
        f.write(cleaned)

    print(f"Wrote: {out} (removed {removed} bg elems, stripped {stripped} bg strokes)")

if __name__ == "__main__":
    main()
