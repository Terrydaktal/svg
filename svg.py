#!/usr/bin/env python3
import argparse
import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from PIL import Image
import vtracer

RGB_RE = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)

def parse_hex6(s: str) -> tuple[int,int,int]:
    s = s.strip().lower()
    if s.startswith("#"): s = s[1:]
    if len(s) != 6:
        raise ValueError("bghex must be 6 hex digits, e.g. FF00FF")
    return (int(s[0:2],16), int(s[2:4],16), int(s[4:6],16))

def color_to_rgb(s: str):
    if not s: return None
    v = s.strip().lower()
    if v == "none": return None
    if v.startswith("#") and len(v) == 7:
        return (int(v[1:3],16), int(v[3:5],16), int(v[5:7],16))
    m = RGB_RE.fullmatch(v)
    if m:
        return tuple(int(x) for x in m.groups())
    return None

def get_style_prop(style: str, key: str):
    # naive CSS style parser
    if not style: return None
    parts = [p.strip() for p in style.split(";") if p.strip()]
    for p in parts:
        if ":" not in p: continue
        k,v = [x.strip() for x in p.split(":",1)]
        if k.lower() == key.lower():
            return v
    return None

def rgb_dist(a, b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)

def preprocess_png(inp, out, bg_rgb, scale):
    img = Image.open(inp).convert("RGBA")
    if scale != 1:
        img = img.resize((img.size[0]*scale, img.size[1]*scale), Image.Resampling.LANCZOS)

    # Composite onto key background (removes alpha)
    bg = Image.new("RGBA", img.size, (*bg_rgb, 255))
    bg.alpha_composite(img)
    bg.convert("RGB").save(out)

def strip_bg(svg_text: str, bg_rgb, tol: float):
    # Keep namespaces stable
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return svg_text

    parent = {c: p for p in root.iter() for c in p}

    def matches_bg(el):
        # check fill and stroke in attributes and style
        fill = el.get("fill")
        stroke = el.get("stroke")
        style = el.get("style","")

        fill2 = get_style_prop(style, "fill")
        stroke2 = get_style_prop(style, "stroke")

        for val in (fill, fill2, stroke, stroke2):
            rgb = color_to_rgb(val) if val else None
            if rgb and rgb_dist(rgb, bg_rgb) <= tol:
                return True
        return False

    removed = 0
    for el in list(root.iter()):
        if matches_bg(el):
            p = parent.get(el)
            if p is not None:
                p.remove(el)
                removed += 1

    if removed == 0:
        return svg_text
    return ET.tostring(root, encoding="unicode")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_png")
    ap.add_argument("output_svg", nargs="?", default=None)
    ap.add_argument("--bghex", default="FF00FF")
    ap.add_argument("--bg-tol", type=float, default=12.0, help="RGB distance tolerance for bg removal")
    ap.add_argument("--scale", type=int, default=4, help="Upscale factor before tracing")
    # VTracer knobs (sane defaults for icons)
    ap.add_argument("--mode", default="spline", choices=["spline","polygon","none"])
    ap.add_argument("--hierarchical", default="stacked", choices=["stacked","cutout"])
    ap.add_argument("--filter-speckle", type=int, default=8)
    ap.add_argument("--color-precision", type=int, default=6)
    ap.add_argument("--layer-difference", type=int, default=20)
    ap.add_argument("--corner-threshold", type=int, default=60)
    ap.add_argument("--length-threshold", type=float, default=6.0)
    ap.add_argument("--splice-threshold", type=int, default=45)
    ap.add_argument("--max-iterations", type=int, default=10)
    ap.add_argument("--path-precision", type=int, default=3)
    args = ap.parse_args()

    inp = args.input_png
    out = args.output_svg or (os.path.splitext(inp)[0] + ".svg")
    bg_rgb = parse_hex6(args.bghex)

    with tempfile.TemporaryDirectory() as td:
        keyed = os.path.join(td, "keyed.png")
        raw_svg = os.path.join(td, "raw.svg")

        preprocess_png(inp, keyed, bg_rgb, args.scale)

        vtracer.convert_image_to_svg_py(
            keyed, raw_svg,
            colormode="color",
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
        svg_text = strip_bg(svg_text, bg_rgb, args.bg_tol)
        with open(out, "w", encoding="utf-8") as f:
            f.write(svg_text)

    print(f"Wrote: {out}")

if __name__ == "__main__":
    main()
