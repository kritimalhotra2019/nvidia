"""Tiny SVG charts: enough for a dataset overview, no plotting library, fully offline.
Each function returns an SVG string. Numbers in, picture out; nothing here estimates anything."""
from __future__ import annotations

import html
import math
from typing import List, Optional, Sequence, Tuple

INK = "#35285e"
LAV = "#b6a6ee"
DEEP = "#5b4ca3"
GOLD = "#ffd166"
GRID = "#e9e2fb"
MUTED = "#6b6480"
FONT = "-apple-system, 'SF Pro Text', 'Helvetica Neue', Helvetica, Arial, sans-serif"


def _esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _fmt(v: float) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e4:
        return f"{v / 1e3:.0f}k"
    if a >= 100 or float(v).is_integer():
        return f"{v:,.0f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.3g}"


def _nice_ticks(lo: float, hi: float, n: int = 5) -> List[float]:
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while t <= hi + step * 0.5:
        ticks.append(round(t, 10))
        t += step
    return ticks


def _frame(w: int, h: int, title: str, body: str, subtitle: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'font-family="{FONT}" font-size="12" role="img" aria-label="{_esc(title)}">'
            f'<rect width="{w}" height="{h}" rx="14" fill="#ffffff"/>'
            f'<text x="18" y="24" font-size="14" font-weight="600" fill="{INK}">{_esc(title)}</text>'
            + (f'<text x="18" y="41" font-size="11" fill="{MUTED}">{_esc(subtitle)}</text>' if subtitle else "")
            + body + "</svg>")


def histogram(title: str, edges: Sequence[float], counts: Sequence[int], subtitle: str = "", w: int = 560, h: int = 300) -> str:
    left, right, top, bottom = 56, 18, 54, 40
    pw, ph = w - left - right, h - top - bottom
    cmax = max(counts) if counts else 1
    yt = _nice_ticks(0, cmax, 4)
    ymax = yt[-1] if yt else cmax or 1
    body = []
    for t in yt:
        y = top + ph - (t / ymax) * ph
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + pw}" y2="{y:.1f}" stroke="{GRID}"/>')
        body.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" fill="{MUTED}">{_fmt(t)}</text>')
    lo, hi = edges[0], edges[-1]
    span = (hi - lo) or 1
    for i, c in enumerate(counts):
        x0 = left + (edges[i] - lo) / span * pw
        x1 = left + (edges[i + 1] - lo) / span * pw
        bh = (c / ymax) * ph
        body.append(f'<rect x="{x0 + 0.5:.1f}" y="{top + ph - bh:.1f}" width="{max(0.5, x1 - x0 - 1):.1f}" height="{bh:.1f}" rx="2" fill="{LAV}">'
                    f'<title>{_fmt(edges[i])} – {_fmt(edges[i + 1])}: {c:,}</title></rect>')
    for t in _nice_ticks(lo, hi, 6):
        if t < lo - span * 0.01 or t > hi + span * 0.01:
            continue
        x = left + (t - lo) / span * pw
        body.append(f'<text x="{x:.1f}" y="{top + ph + 16}" text-anchor="middle" fill="{MUTED}">{_fmt(t)}</text>')
    body.append(f'<line x1="{left}" y1="{top + ph}" x2="{left + pw}" y2="{top + ph}" stroke="{INK}" stroke-opacity="0.25"/>')
    body.append(f'<text x="{left + pw / 2:.0f}" y="{h - 8}" text-anchor="middle" fill="{MUTED}">count per bin</text>')
    return _frame(w, h, title, "".join(body), subtitle)


def bars(title: str, labels: Sequence[str], values: Sequence[float], subtitle: str = "", w: int = 560, h: Optional[int] = None,
         color: str = LAV, value_fmt=_fmt, pct_of: Optional[float] = None) -> str:
    """Horizontal bars, one per label."""
    n = len(labels)
    row = 24
    h = h or (60 + n * row + 24)
    left = 18 + min(180, max((len(str(l)) for l in labels), default=4) * 6.5)
    right = 70
    pw = w - left - right
    vmax = max(values) if values else 1
    body = []
    for i, (lab, v) in enumerate(zip(labels, values)):
        y = 54 + i * row
        bw = (v / vmax) * pw if vmax else 0
        body.append(f'<text x="{left - 8}" y="{y + 15}" text-anchor="end" fill="{INK}">{_esc(str(lab)[:28])}</text>')
        body.append(f'<rect x="{left}" y="{y + 4}" width="{bw:.1f}" height="16" rx="5" fill="{color}"><title>{_esc(lab)}: {value_fmt(v)}</title></rect>')
        txt = value_fmt(v) + (f" ({v / pct_of * 100:.0f}%)" if pct_of else "")
        body.append(f'<text x="{left + bw + 6:.1f}" y="{y + 16}" fill="{MUTED}">{_esc(txt)}</text>')
    return _frame(w, h, title, "".join(body), subtitle)


def line(title: str, xs: Sequence[str], ys: Sequence[float], subtitle: str = "", w: int = 560, h: int = 260) -> str:
    left, right, top, bottom = 56, 18, 54, 44
    pw, ph = w - left - right, h - top - bottom
    n = len(xs)
    if n == 0:
        return _frame(w, h, title, "", subtitle)
    ymax = max(ys) or 1
    yt = _nice_ticks(0, ymax, 4)
    ymax = yt[-1] if yt else ymax
    body = []
    for t in yt:
        y = top + ph - (t / ymax) * ph
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + pw}" y2="{y:.1f}" stroke="{GRID}"/>')
        body.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" fill="{MUTED}">{_fmt(t)}</text>')
    pts = []
    for i, v in enumerate(ys):
        x = left + (i / max(1, n - 1)) * pw
        y = top + ph - (v / ymax) * ph
        pts.append(f"{x:.1f},{y:.1f}")
    body.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{DEEP}" stroke-width="2.2" stroke-linejoin="round"/>')
    if n <= 60:
        for p, lab, v in zip(pts, xs, ys):
            x, y = p.split(",")
            body.append(f'<circle cx="{x}" cy="{y}" r="3" fill="{DEEP}"><title>{_esc(lab)}: {_fmt(v)}</title></circle>')
    step = max(1, math.ceil(n / 8))
    for i in range(0, n, step):
        x = left + (i / max(1, n - 1)) * pw
        body.append(f'<text x="{x:.1f}" y="{top + ph + 16}" text-anchor="middle" fill="{MUTED}">{_esc(str(xs[i])[:10])}</text>')
    return _frame(w, h, title, "".join(body), subtitle)


def missingness(title: str, columns: Sequence[str], pct: Sequence[float], w: int = 560) -> str:
    return bars(title, columns, list(pct), subtitle="share of rows missing, per column", w=w, color=GOLD,
                value_fmt=lambda v: f"{v:.1f}%")


def swatch_table(rows: Sequence[Tuple[str, str]], w: int = 560) -> str:
    """Not a chart: a compact key/value block rendered as SVG for the report header."""
    h = 20 + len(rows) * 20
    body = []
    for i, (k, v) in enumerate(rows):
        y = 18 + i * 20
        body.append(f'<text x="18" y="{y}" fill="{MUTED}">{_esc(k)}</text><text x="200" y="{y}" fill="{INK}" font-weight="600">{_esc(v)}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="{FONT}" font-size="12">'
            + "".join(body) + "</svg>")


def scatter(title: str, xs: Sequence[float], ys: Sequence[float], subtitle: str = "", w: int = 560, h: int = 360,
            xlabel: str = "", ylabel: str = "") -> str:
    left, right, top, bottom = 60, 18, 54, 48
    pw, ph = w - left - right, h - top - bottom
    if not xs:
        return _frame(w, h, title, "", subtitle)
    xlo, xhi, ylo, yhi = min(xs), max(xs), min(ys), max(ys)
    xt, yt = _nice_ticks(xlo, xhi, 6), _nice_ticks(ylo, yhi, 5)
    xlo, xhi = min(xlo, xt[0]), max(xhi, xt[-1])
    ylo, yhi = min(ylo, yt[0]), max(yhi, yt[-1])
    xs_, ys_ = (xhi - xlo) or 1, (yhi - ylo) or 1
    body = []
    for t in yt:
        y = top + ph - (t - ylo) / ys_ * ph
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + pw}" y2="{y:.1f}" stroke="{GRID}"/>')
        body.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" fill="{MUTED}">{_fmt(t)}</text>')
    for t in xt:
        x = left + (t - xlo) / xs_ * pw
        body.append(f'<text x="{x:.1f}" y="{top + ph + 16}" text-anchor="middle" fill="{MUTED}">{_fmt(t)}</text>')
    pts = []
    for x, y in zip(xs, ys):
        px = left + (x - xlo) / xs_ * pw
        py = top + ph - (y - ylo) / ys_ * ph
        pts.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.6" fill="{DEEP}" fill-opacity="0.45"/>')
    body.append("".join(pts))
    if xlabel:
        body.append(f'<text x="{left + pw / 2:.0f}" y="{h - 8}" text-anchor="middle" fill="{MUTED}">{_esc(xlabel)}</text>')
    if ylabel:
        body.append(f'<text transform="translate(14 {top + ph / 2:.0f}) rotate(-90)" text-anchor="middle" fill="{MUTED}">{_esc(ylabel)}</text>')
    return _frame(w, h, title, "".join(body), subtitle)


def heatmap(title: str, labels: Sequence[str], matrix: Sequence[Sequence[float]], subtitle: str = "", w: int = 560) -> str:
    """Correlation matrix: blue for negative, violet for positive, value printed in each cell."""
    n = len(labels)
    if n == 0:
        return _frame(w, 120, title, "", subtitle)
    left = 18 + min(150, max(len(str(l)) for l in labels) * 6.2)
    cell = max(22, min(40, (w - left - 18) // n))
    top = 54 + min(110, max(len(str(l)) for l in labels) * 5.2)
    h = int(top + n * cell + 20)
    body = []
    for j, lab in enumerate(labels):
        x = left + j * cell + cell / 2
        body.append(f'<text transform="translate({x:.1f} {top - 6}) rotate(-55)" fill="{MUTED}" font-size="10">{_esc(str(lab)[:22])}</text>')
    for i, lab in enumerate(labels):
        y = top + i * cell
        body.append(f'<text x="{left - 8}" y="{y + cell / 2 + 4:.1f}" text-anchor="end" fill="{INK}" font-size="10">{_esc(str(lab)[:24])}</text>')
        for j in range(n):
            v = matrix[i][j]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                fill, txt = "#f3f0fa", ""
            else:
                a = min(1.0, abs(float(v)))
                base = (91, 76, 163) if v >= 0 else (58, 134, 196)
                r = int(255 + (base[0] - 255) * a); g = int(255 + (base[1] - 255) * a); b = int(255 + (base[2] - 255) * a)
                fill, txt = f"rgb({r},{g},{b})", f"{v:.2f}"
            x = left + j * cell
            body.append(f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{cell - 2}" height="{cell - 2}" rx="4" fill="{fill}"><title>{_esc(labels[i])} × {_esc(labels[j])}: {txt}</title></rect>')
            if txt and cell >= 26:
                col = "#ffffff" if abs(float(v)) > 0.55 else INK
                body.append(f'<text x="{x + cell / 2:.1f}" y="{y + cell / 2 + 3.5:.1f}" text-anchor="middle" fill="{col}" font-size="9">{txt}</text>')
    return _frame(w, h, title, "".join(body), subtitle)


def dots(title: str, labels: Sequence[str], values: Sequence[float], lows: Sequence[float], highs: Sequence[float],
         subtitle: str = "", w: int = 560, zero_line: bool = True) -> str:
    """Point estimates with interval bars, one row per label (coefficient plot, group means with CIs)."""
    n = len(labels)
    row = 26
    h = 70 + n * row + 24
    left = 18 + min(200, max((len(str(l)) for l in labels), default=4) * 6.5)
    right = 24
    pw = w - left - right
    lo = min(list(lows) + ([0.0] if zero_line else []))
    hi = max(list(highs) + ([0.0] if zero_line else []))
    span = (hi - lo) or 1.0
    lo -= span * 0.05; hi += span * 0.05; span = hi - lo
    xpos = lambda v: left + (v - lo) / span * pw
    body = []
    for t in _nice_ticks(lo, hi, 6):
        if t < lo or t > hi:
            continue
        x = xpos(t)
        body.append(f'<line x1="{x:.1f}" y1="54" x2="{x:.1f}" y2="{54 + n * row}" stroke="{GRID}"/>')
        body.append(f'<text x="{x:.1f}" y="{62 + n * row}" text-anchor="middle" fill="{MUTED}">{_fmt(t)}</text>')
    if zero_line and lo < 0 < hi:
        x = xpos(0)
        body.append(f'<line x1="{x:.1f}" y1="54" x2="{x:.1f}" y2="{54 + n * row}" stroke="{INK}" stroke-opacity="0.4" stroke-dasharray="3 3"/>')
    for i, (lab, v, a, b) in enumerate(zip(labels, values, lows, highs)):
        y = 54 + i * row + row / 2
        body.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" fill="{INK}">{_esc(str(lab)[:30])}</text>')
        body.append(f'<line x1="{xpos(a):.1f}" y1="{y:.1f}" x2="{xpos(b):.1f}" y2="{y:.1f}" stroke="{DEEP}" stroke-width="2"/>')
        body.append(f'<circle cx="{xpos(v):.1f}" cy="{y:.1f}" r="4.5" fill="{DEEP}"><title>{_esc(lab)}: {_fmt(v)} [{_fmt(a)}, {_fmt(b)}]</title></circle>')
    return _frame(w, h, title, "".join(body), subtitle)
