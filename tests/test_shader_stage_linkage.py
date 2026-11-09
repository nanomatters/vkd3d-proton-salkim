#!/usr/bin/env python3
# Copyright 2026 Erhan Bilgili
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Extract the production PSO compile-argument initializer for CPU shader tests."""

import argparse
from pathlib import Path

from test_queue_placement import function

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", required=True, type=Path)
    args = parser.parse_args()
    source = (ROOT / "libs/vkd3d/state.c").read_text()
    args.generate.write_text(function(source, "d3d12_pipeline_state_init_compile_arguments") + "\n")
