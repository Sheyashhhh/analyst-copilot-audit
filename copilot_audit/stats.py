"""Small, dependency-free statistics used by the report."""
from __future__ import annotations

import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value. b = only A correct, c = only B correct (paired questions)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2**n
    return min(1.0, 2 * p)


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def paired_bootstrap_ci(a: list[int], b: list[int], n: int = 5000, seed: int = 7) -> tuple[float, float, float]:
    """Mean paired difference (b - a) with a percentile bootstrap 95% CI, resampling questions."""
    import random
    rng = random.Random(seed)
    d = [y - x for x, y in zip(a, b)]
    m = len(d)
    if m == 0:
        return (0.0, 0.0, 0.0)
    means = sorted(sum(d[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return (sum(d) / m, means[int(0.025 * n)], means[int(0.975 * n) - 1])


def certify_n(p_hat: float = 1.0, floor: float = 0.90) -> int:
    """Smallest number of questions at which a Wilson 95% lower bound reaches `floor` if accuracy is p_hat."""
    for n in range(1, 2000):
        k = round(p_hat * n)
        if wilson(k, n)[0] >= floor:
            return n
    return 2000
