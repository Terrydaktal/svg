#!/usr/bin/env python3
import sys
import os
import subprocess
import argparse

# Ensure we can import svg.py from the same directory
DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DIR)

import svg

def main():
    # 1. Parse arguments. We use svg.py's parser to identify known args.
    #    Everything else is assumed to be for 'scour'.
    parser = svg.get_parser()
    
    # parse_known_args returns (known_args, unknown_args_list)
    args, scour_args = parser.parse_known_args()
    
    # Determine output file
    # If not provided, svg.py defaults to replacing extension, but we need to know it
    if args.output_svg:
        out_file = args.output_svg
    else:
        out_file = os.path.splitext(args.input_png)[0] + ".svg"
        # Update args to reflect this, so svg.generate_svg (if it used it) knows
        args.output_svg = out_file

    # 2. Run svg.py generation
    try:
        # returns (xml_string, removed_count, stripped_count)
        raw_svg_content, removed, stripped = svg.generate_svg(args)
        print(f"Generated SVG (removed {removed} bg elems, stripped {stripped} bg strokes)", file=sys.stderr)
    except Exception as e:
        print(f"Error generating SVG: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Prepare scour command
    # Default scour options as requested
    default_scour_opts = [
        "--set-precision=2",
        "--strip-xml-prolog",
        "--remove-metadata",
        "--enable-id-stripping",
        "--shorten-ids",
        "--indent=none"
    ]
    
    # We combine defaults with user-provided scour_args.
    # User args should override defaults if they conflict, but scour uses last-wins usually?
    # Or we can just append them.
    
    cmd = ["scour"] + default_scour_opts + scour_args
    
    # 4. Run scour, piping raw_svg_content to stdin, and writing to out_file
    print(f"Running scour: {' '.join(cmd)}", file=sys.stderr)
    
    try:
        # check if scour is available
        subprocess.run(["scour", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
         # If system scour is missing, we might fail.
         # The user said "use scour from apt install". 
         # But wait, I installed 'scour' in the venv earlier too (via pip). 
         # I should prefer the one in the path, which in this script (run by venv python) might be the venv one if activated?
         # Actually subprocess.run searches PATH.
         # If I want to be safe, I can check if 'scour' is in the venv bin?
         # But the user specifically said "use scour from apt install". 
         # So 'scour' command should be fine if it's in /usr/bin/scour.
         pass

    # We open the output file for writing
    with open(out_file, "w", encoding="utf-8") as f:
        # Run scour
        res = subprocess.run(
            cmd,
            input=raw_svg_content,
            stdout=f,
            text=True
        )
        
    if res.returncode != 0:
        print("Error running scour.", file=sys.stderr)
        sys.exit(res.returncode)
        
    print(f"Wrote optimized SVG to: {out_file}", file=sys.stderr)

if __name__ == "__main__":
    main()
