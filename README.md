# SVG Icon Vectorizer

A specialized tool for converting small, anti-aliased PNG icons (specifically tailored for waveform/cursor style icons) into clean, optimized, and pixel-perfect SVGs.

## Project Structure

- `svg`: The primary entry point. A bash wrapper that ensures the script runs within the local virtual environment.
- `svg_wrapper.py`: The orchestration layer. It handles mixed argument parsing, passes parameters to the generator, and pipes the output through the `scour` optimizer.
- `svg.py`: The core logic. Performs image preprocessing (alpha-flattening, morphology, blurring) and interfaces with `vtracer` for vectorization.
- `icon.png`: The sample input icon.
- `.venv/`: Local Python virtual environment containing dependencies (`numpy`, `Pillow`, `vtracer`, `scour`).

## The Pipeline

1.  **Preprocessing (`svg.py`)**:
    - **Upscaling**: The input is scaled (default 8x) to provide more detail for the tracer.
    - **Alpha Flattening**: Pixels are classified as foreground or background based on a configurable `--alpha-cutoff`.
    - **Palette Mapping**: Foreground pixels are snapped to a strict palette (White/Blue) using nearest-neighbor color classification to eliminate anti-aliasing artifacts.
    - **Morphology**: A morphological "close" operation fills small gaps and smoothes jagged edges.
2.  **Vectorization (`vtracer`)**:
    - The flattened, high-contrast image is traced into raw SVG paths.
3.  **Post-processing (`svg.py`)**:
    - The "key color" (Magenta) used for background flattening is stripped from the resulting XML.
4.  **Optimization (`scour`)**:
    - The raw SVG is piped through `scour` to reduce precision, remove metadata, shorten IDs, and minimize file size.

## Installation

Ensure you have `scour` installed on your system (via `apt` or `pip`) and the local virtual environment set up:

```bash
# Install system optimizer
sudo apt install scour

# Setup venv and dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install numpy Pillow vtracer-python scour
```

## Usage

The `svg` command accepts both vectorizer and optimizer flags:

```bash
./svg input.png [output.svg] [options]
```

### Key Options

**Vectorizer:**
- `--scale 8`: Upscale factor before tracing.
- `--alpha-cutoff 140`: Sensitivity for foreground detection.
- `--mask-blur 1.1`: Softens the matte before morphology.
- `--morph 5`: Size of the cleaning filter.
- `--path-precision 0`: Control path accuracy (0 is highest).

**Optimizer (passed to Scour):**
- `--set-precision=2`: Decimal precision for coordinates.
- `--indent=none`: Minimizes whitespace.
- `--shorten-ids`: Renames IDs to minimize size.

### Example

```bash
./svg icon.png icon.svg --scale 8 --morph 5 --set-precision=1
```

## Inputs and Outputs

- **Input**: Any PNG with transparency. Best results on high-contrast icons.
- **Output**: A clean, minified SVG file.
- **Debug Artifacts**: Use `--save-flat` to see the intermediate `.flat.png` used for tracing.
