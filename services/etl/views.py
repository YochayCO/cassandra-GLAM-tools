def percentile_cont(values, p):
    if not values:
        raise ValueError("empty")
    xs = sorted(values)
    n = len(xs)
    if n == 1:
        return float(xs[0])
    idx = (n - 1) * p
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    w = idx - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def stats_row(accesses):
    tot = sum(accesses)
    n = len(accesses)
    return tot, n, tot / n, percentile_cont(accesses, 0.5)
