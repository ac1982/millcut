"""Prespecified dimensionless geometry features; no names or source-family labels."""
import csv, math, statistics


def extract(items, W, H, base):
    widths = [int(it['WIDTH']) / W for it in items]
    heights = [int(it['HEIGHT']) / H for it in items]
    areas = [w * h for w, h in zip(widths, heights)]
    def cv(v):
        return statistics.pstdev(v) / statistics.mean(v)
    def q(v, p):
        s = sorted(v)
        return s[round((len(s) - 1) * p)]
    f = dict(base)
    f.update(width_cv=cv(widths), height_cv=cv(heights), area_cv=cv(areas),
             mean_width=statistics.mean(widths), mean_height=statistics.mean(heights),
             max_width=max(widths), max_height=max(heights),
             height_q90_q10=q(heights, .9) / q(heights, .1), width_q90_q10=q(widths, .9) / q(widths, .1),
             height_unique_fraction=len(set(heights)) / len(heights),
             tall_fraction=sum(h > .5 for h in heights) / len(heights), wide_fraction=sum(w > .5 for w in widths) / len(widths))
    f['width_height_correlation'] = statistics.correlation(widths, heights) if len(set(widths)) > 1 and len(set(heights)) > 1 else 0.0
    support = [sum(w for w, hh in zip(widths, heights) if .9 * h <= hh <= h) for h in heights]
    f['near_height_width_support_mean'] = statistics.mean(support)
    f['near_height_width_support_min'] = min(support)
    f['near_height_width_support_ge_one'] = sum(s >= 1 for s in support) / len(support)
    return f


def read(name, data, features):
    with (data / f'{name}_items.csv').open() as f:
        items = list(csv.DictReader(f))
    with (data / f'{name}_bins.csv').open() as f:
        bt = next(csv.DictReader(f))
    W, H = int(bt['WIDTH']), int(bt['HEIGHT'])
    return extract(items, W, H, features(items, W, H))


def accept(f, policy):
    if policy['kind'] == 'default_only':
        return False
    if policy['kind'] == 'original_gate':
        return True
    return f[policy['feature']] >= policy['threshold'] if policy['direction'] == 'ge' else f[policy['feature']] <= policy['threshold']
