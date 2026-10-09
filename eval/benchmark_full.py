"""Compatibility entry point for the corrected, isolated evaluator.

Historical benchmark_full.json is preserved, but its original scoring and timing
must not be used as validated final evidence. New runs have versioned filenames.
"""

from eval.metrics import blast_f1 as _f1
from eval.research_benchmark import main

__all__ = ["_f1", "main"]

if __name__ == "__main__":
    main()
