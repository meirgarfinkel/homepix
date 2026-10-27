"""Turning search scores into clear matches and possibly related photos."""

import numpy as np

from .config import MATCH_STRICTNESS, MAX_NEAR, NEAR_STRICTNESS


def split_matches(scores: np.ndarray):
    """Split photos into clear matches and possibly related ones, best first.
    Photos below both bars are left out.

    Every photo gets a score, and there's no fixed number that means "match":
    it varies by search. So instead we look at how this search scored across the
    whole library and keep photos that stand well above the typical one.
    Median-based statistics are used so a handful of strong matches don't skew it.
    """
    order = np.argsort(-scores)
    if len(scores) < 8:  # too few photos to tell what's typical
        return order, order[:0]
    typical = np.median(scores)
    spread = max(1.4826 * np.median(np.abs(scores - typical)), 0.005)
    n_clear = int((scores >= typical + MATCH_STRICTNESS * spread).sum())
    n_near = int((scores >= typical + NEAR_STRICTNESS * spread).sum())
    return order[:n_clear], order[n_clear:n_near][:MAX_NEAR]
