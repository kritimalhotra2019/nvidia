"""create_chart: publication-quality charts with matplotlib, written as SVG (for the workspace) and PNG (for Word,
PowerPoint and vision models), plus the numbers that went into them and a caption that states what the chart shows.
The caption is a template filled with computed values; nothing here estimates anything."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..registry import JobContext, tool, InvalidArguments
from ..project import slug
from .. import svg as _svg
from .datasets import read_dataset, SAMPLE_ROWS, MAX_FULL_ROWS
from .document_style import STYLE_SCHEMA, normalize_style, heading_color

MAX_POINTS = 4000
KINDS = ["histogram", "bar", "grouped_bar", "stacked_bar", "line", "area", "scatter", "box", "heatmap"]

# Squipi's palette: lavender and indigo, with gold and teal for contrast
PALETTE = ["#5b4ca3", "#b6a6ee", "#ffd166", "#3fb8a8", "#f28c8c", "#8fa8ff", "#c9a86a", "#7fd1a5", "#e39bd8", "#9aa0b4"]
INK, MUTED, GRID = "#35285e", "#6b6480", "#e9e2fb"


def _mpl(style):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.dpi": 110, "savefig.dpi": 160, "figure.facecolor": "white", "axes.facecolor": "white",
        "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"], "font.size": 10.5, "text.color": INK,
        "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "axes.titlecolor": INK, "axes.titleweight": "semibold", "axes.titlesize": 13,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.axisbelow": True, "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelsize": 9.5, "ytick.labelsize": 9.5,
        "legend.frameon": False, "legend.fontsize": 9.5, "axes.prop_cycle": matplotlib.cycler(color=PALETTE),
        "svg.fonttype": "none",
    })
    if style:
        plt.rcParams.update({
            "figure.facecolor": style.get("background_color", "white"), "axes.facecolor": style.get("background_color", "white"),
            "axes.prop_cycle": matplotlib.cycler(color=_palette(style)),
            "axes.labelcolor": style.get("body_color", MUTED), "xtick.color": style.get("body_color", MUTED), "ytick.color": style.get("body_color", MUTED),
            "text.color": style.get("body_color", INK), "axes.titlecolor": heading_color(style, INK),
        })
        if "font_family" in style:
            # A missing local font uses matplotlib's bundled fallback; Office
            # still receives the requested family as an editable font setting.
            from matplotlib import font_manager
            chosen = font_manager.FontProperties(family=style["font_family"])
            try:
                font_manager.findfont(chosen, fallback_to_default=False)
                plt.rcParams["font.family"] = [style["font_family"], "DejaVu Sans"]
            except ValueError:
                plt.rcParams["font.family"] = ["DejaVu Sans"]
    return plt


def _palette(style):
    colors = style.get("chart_palette") or ([style["accent_color"]] if style.get("accent_color") else PALETTE)
    return [colors[index % len(colors)] for index in range(max(3, len(colors)))]


def _fmt(v: float) -> str:
    return _svg._fmt(v)


def _finish(plt, fig, ax, title: str, subtitle: str, note: str, out: Path, stem: str, style) -> Tuple[str, str]:
    fig.suptitle(title, x=0.01, ha="left", fontsize=13.5, fontweight="semibold", color=heading_color(style, INK), y=0.985)
    if subtitle:
        fig.text(0.01, 0.925, subtitle, ha="left", fontsize=10, color=style.get("body_color", MUTED))
    if note:
        fig.text(0.01, 0.01, note, ha="left", fontsize=8.5, color=style.get("body_color", MUTED))
    fig.tight_layout(rect=(0, 0.035 if note else 0, 1, 0.90 if subtitle else 0.94))
    svg_path = out / f"{slug(stem, 60)}.svg"
    png_path = out / f"{slug(stem, 60)}.png"
    fig.savefig(svg_path, format="svg", bbox_inches="tight", pad_inches=0.2)
    fig.savefig(png_path, format="png", bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)
    return str(svg_path), str(png_path)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df.columns:
        return df[name]
    close = [c for c in df.columns if str(c).lower() == str(name).lower()]
    if close:
        return df[close[0]]
    raise InvalidArguments(f"no column '{name}'. Columns: {', '.join(map(str, df.columns[:40]))}")


def _period(d: pd.Series) -> Tuple[str, str]:
    span = (d.max() - d.min()).days
    return ("D", "day") if span <= 120 else ("M", "month") if span <= 366 * 6 else ("Y", "year")


def _annotate_bars(ax, bars, values, horizontal: bool, fmt=_fmt, ink=INK):
    for b, v in zip(bars, values):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        if horizontal:
            ax.annotate(fmt(v), (b.get_width(), b.get_y() + b.get_height() / 2), xytext=(4, 0), textcoords="offset points", va="center", fontsize=9, color=ink)
        else:
            ax.annotate(fmt(v), (b.get_x() + b.get_width() / 2, b.get_height()), xytext=(0, 3), textcoords="offset points", ha="center", fontsize=9, color=ink)


def draw(out: Path, kind: str, df: pd.DataFrame, x: str, y: Optional[str] = None, group: Optional[str] = None, agg: str = "mean",
         bins: Optional[int] = None, top: int = 12, title: Optional[str] = None, subtitle: Optional[str] = None, note: str = "",
         horizontal: Optional[bool] = None, highlight: bool = True, stem: Optional[str] = None, style: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Draw one chart; returns paths, the plotted data and a caption. Used by create_chart, find_insights and report_dataset."""
    style = normalize_style(style)
    plt = _mpl(style)
    palette = _palette(style)
    ink, muted = style.get("body_color", INK), style.get("body_color", MUTED)
    xs = _col(df, x)
    data: Dict[str, Any] = {"kind": kind, "x": x, "y": y, "group": group, "n": int(len(df))}
    caption = ""
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    stem = stem or f"{kind}-{x}" + (f"-{y}" if y else "")

    if kind == "histogram":
        v = pd.to_numeric(xs, errors="coerce").dropna().astype(float)
        if len(v) == 0:
            raise InvalidArguments(f"'{x}' has no numeric values")
        b = bins or int(min(40, max(8, round(math.sqrt(len(v))))))
        counts, edges = np.histogram(v, bins=b)
        ax.hist(v, bins=edges, color=palette[1], edgecolor="white", linewidth=0.6)
        med, mean = float(v.median()), float(v.mean())
        ax.axvline(med, color=palette[0], linewidth=1.6, linestyle="--")
        ax.annotate(f"median {_fmt(med)}", (med, ax.get_ylim()[1] * 0.95), xytext=(6, 0), textcoords="offset points", color=palette[0], fontsize=9.5)
        ax.set_xlabel(x); ax.set_ylabel("rows")
        data.update({"edges": edges.tolist(), "counts": counts.tolist(), "count": int(len(v)), "median": med, "mean": mean, "min": float(v.min()), "max": float(v.max())})
        title = title or f"How {x} is distributed"
        subtitle = subtitle or f"{len(v):,} values · median {_fmt(med)} · mean {_fmt(mean)}"
        skew = " The mean sits above the median, so a few large values pull the average up." if mean > med * 1.15 and med > 0 else ""
        caption = f"Each bar counts the rows whose {x} falls in that range. Half the rows are below {_fmt(med)}; values run from {_fmt(float(v.min()))} to {_fmt(float(v.max()))}.{skew}"

    elif kind in ("bar", "grouped_bar", "stacked_bar"):
        if y:
            yv = pd.to_numeric(_col(df, y), errors="coerce")
            frame = pd.DataFrame({"x": xs.astype(str).where(xs.notna(), "(missing)"), "y": yv})
            if group and kind != "bar":
                frame["g"] = _col(df, group).astype(str).where(_col(df, group).notna(), "(missing)")
                keep = frame.groupby("x")["y"].count().sort_values(ascending=False).head(top).index
                frame = frame[frame["x"].isin(keep)]
                pv = frame.pivot_table(index="x", columns="g", values="y", aggfunc=agg)
                pv = pv.loc[pv.mean(axis=1).sort_values(ascending=False).index]
                if pv.shape[1] > 8:
                    pv = pv[pv.count().sort_values(ascending=False).index[:8]]
                pv.plot(kind="bar", stacked=(kind == "stacked_bar"), ax=ax, width=0.78, edgecolor="white", linewidth=0.5)
                ax.set_ylabel(f"{agg} of {y}"); ax.set_xlabel(x); ax.legend(title=group, ncol=min(4, pv.shape[1]))
                plt.setp(ax.get_xticklabels(), rotation=0 if pv.shape[0] <= 6 else 30, ha="right" if pv.shape[0] > 6 else "center")
                data.update({"agg": agg, "labels": [str(i) for i in pv.index], "series": {str(c): [None if pd.isna(v) else float(v) for v in pv[c]] for c in pv.columns}})
                title = title or f"{agg.title()} {y} by {x} and {group}"
                subtitle = subtitle or f"{len(frame):,} rows"
                best = pv.mean(axis=1).idxmax()
                caption = f"Bars compare the {agg} of {y} across {x}, split by {group}. {best} is highest on average across the groups."
            else:
                g = frame.dropna(subset=["y"]).groupby("x")["y"]
                series = {"count": g.count(), "sum": g.sum(), "mean": g.mean(), "median": g.median(), "min": g.min(), "max": g.max()}[agg]
                counts = g.count()
                series = series.sort_values(ascending=False).head(top)
                labels, values = [str(i) for i in series.index], [float(v) for v in series.values]
                horiz = horizontal if horizontal is not None else (len(labels) > 6 or max(len(l) for l in labels) > 12)
                colors = [palette[0] if (highlight and i == 0) else palette[1] for i in range(len(labels))]
                bars = ax.barh(labels[::-1], values[::-1], color=colors[::-1]) if horiz else ax.bar(labels, values, color=colors)
                _annotate_bars(ax, bars, values[::-1] if horiz else values, horiz, ink=ink)
                if horiz:
                    ax.set_xlabel(f"{agg} of {y}")
                else:
                    ax.set_ylabel(f"{agg} of {y}"); plt.setp(ax.get_xticklabels(), rotation=0 if len(labels) <= 6 else 30, ha="right" if len(labels) > 6 else "center")
                data.update({"agg": agg, "labels": labels, "values": values, "n_per_group": [int(counts.get(l, 0)) for l in series.index]})
                title = title or f"{agg.title()} {y} by {x}"
                subtitle = subtitle or f"{len(series)} groups" + (f" of {frame['x'].nunique()}" if frame["x"].nunique() > len(series) else "") + f" · {int(frame['y'].notna().sum()):,} rows"
                caption = (f"Each bar is the {agg} of {y} for one {x}, highest first. {labels[0]} leads at {_fmt(values[0])}"
                           + (f", {labels[-1]} is lowest at {_fmt(values[-1])}" if len(values) > 1 else "") + "."
                           + (" Groups with few rows have less reliable values." if min(data["n_per_group"]) < 30 else ""))
        else:
            vc = xs.dropna().astype(str).value_counts()
            total = int(vc.sum())
            shown = vc.head(top)
            labels, values = [str(i) for i in shown.index], [int(v) for v in shown.values]
            horiz = horizontal if horizontal is not None else (len(labels) > 6 or max(len(l) for l in labels) > 12)
            colors = [palette[0] if (highlight and i == 0) else palette[1] for i in range(len(labels))]
            bars = ax.barh(labels[::-1], values[::-1], color=colors[::-1]) if horiz else ax.bar(labels, values, color=colors)
            _annotate_bars(ax, bars, values[::-1] if horiz else values, horiz, fmt=lambda v: f"{int(v):,} ({100 * v / total:.0f}%)", ink=ink)
            if horiz:
                ax.set_xlabel("rows")
            else:
                ax.set_ylabel("rows"); plt.setp(ax.get_xticklabels(), rotation=0 if len(labels) <= 6 else 30, ha="right" if len(labels) > 6 else "center")
            data.update({"agg": "count", "labels": labels, "values": values, "total": total})
            title = title or f"Rows in each {x} group"
            subtitle = subtitle or f"{len(vc):,} distinct values · {total:,} rows"
            caption = f"How many rows fall in each {x}. {labels[0]} is the largest group with {values[0]:,} rows ({100 * values[0] / total:.0f}% of the total)." + (f" {len(vc) - len(shown)} smaller groups are not shown." if len(vc) > len(shown) else "")

    elif kind in ("line", "area"):
        if not y:
            raise InvalidArguments(f"{kind} needs y")
        yv = pd.to_numeric(_col(df, y), errors="coerce")
        xd = pd.to_datetime(xs, errors="coerce") if (xs.dtype == object or pd.api.types.is_datetime64_any_dtype(xs)) else xs
        frame = pd.DataFrame({"x": xd, "y": yv})
        if group:
            frame["g"] = _col(df, group).astype(str)
        frame = frame.dropna(subset=["x", "y"]).sort_values("x")
        if len(frame) == 0:
            raise InvalidArguments("nothing to plot after dropping missing values")
        is_date = pd.api.types.is_datetime64_any_dtype(frame["x"])
        if is_date:
            per, per_name = _period(frame["x"])
            frame["p"] = frame["x"].dt.to_period(per)
        else:
            frame["p"] = frame["x"]
        series_data: Dict[str, List[Optional[float]]] = {}
        if group:
            groups = frame["g"].value_counts().head(8).index
            for gname in groups:
                gf = frame[frame["g"] == gname].groupby("p")["y"].agg(agg)
                ax.plot([str(i) for i in gf.index] if is_date else gf.index, gf.values, marker="o" if len(gf) <= 40 else None, linewidth=2, label=str(gname))
                series_data[str(gname)] = [float(v) for v in gf.values]
            ax.legend(title=group, ncol=min(4, len(groups)))
            labels = [str(i) for i in sorted(frame["p"].unique())]
        else:
            gf = frame.groupby("p")["y"].agg(agg)
            if len(gf) > 500:
                gf = gf.iloc[:: int(math.ceil(len(gf) / 500))]
            xx = [str(i) for i in gf.index] if is_date else gf.index
            ax.plot(xx, gf.values, marker="o" if len(gf) <= 40 else None, linewidth=2.2, color=palette[0])
            if kind == "area":
                ax.fill_between(range(len(gf)) if is_date else gf.index, gf.values, alpha=0.18, color=palette[0])
            labels = [str(i) for i in gf.index]
            series_data[y] = [float(v) for v in gf.values]
            if highlight and len(gf) >= 2:
                i_max = int(np.argmax(gf.values)); i_min = int(np.argmin(gf.values))
                for i, lab in ((i_max, "peak"), (i_min, "low")):
                    ax.annotate(f"{lab} {_fmt(float(gf.values[i]))}", (xx[i] if not is_date else i, gf.values[i]), xytext=(0, 8 if lab == "peak" else -14), textcoords="offset points", ha="center", fontsize=9, color=ink)
        if is_date and len(labels) > 12:
            step = max(1, len(labels) // 10)
            ax.set_xticks(range(0, len(labels), step)); ax.set_xticklabels(labels[::step], rotation=30, ha="right")
        ax.set_ylabel(f"{agg} {y}"); ax.set_xlabel(x if not is_date else f"{x} ({per_name})" if is_date else x)
        data.update({"agg": agg, "labels": labels, "series": series_data})
        title = title or f"{y} over {x}" + (f" by {group}" if group else "")
        subtitle = subtitle or f"{agg} per {per_name if is_date else x} · {len(frame):,} rows"
        if not group and len(series_data[y]) >= 2:
            first, last = series_data[y][0], series_data[y][-1]
            change = (last - first) / abs(first) * 100 if first else float("nan")
            direction = "rose" if last > first else "fell" if last < first else "was flat"
            caption = f"The line is the {agg} of {y} per {per_name if is_date else x}. It {direction} from {_fmt(first)} at {labels[0]} to {_fmt(last)} at {labels[-1]}" + (f" ({change:+.0f}%)" if not math.isnan(change) else "") + "."
        else:
            caption = f"One line per {group}: the {agg} of {y} over {x}. Compare the lines' levels and directions, not single points."

    elif kind == "scatter":
        if not y:
            raise InvalidArguments("scatter needs y")
        xv = pd.to_numeric(xs, errors="coerce"); yv = pd.to_numeric(_col(df, y), errors="coerce")
        frame = pd.DataFrame({"x": xv, "y": yv})
        if group:
            frame["g"] = _col(df, group).astype(str)
        frame = frame.dropna(subset=["x", "y"])
        if len(frame) == 0:
            raise InvalidArguments("no numeric pairs to plot")
        shown = frame.sample(MAX_POINTS, random_state=0) if len(frame) > MAX_POINTS else frame
        corr = float(frame["x"].corr(frame["y"])) if len(frame) > 2 else None
        if group:
            for i, (gname, gf) in enumerate(shown.groupby("g")):
                if i >= 8:
                    break
                ax.scatter(gf["x"], gf["y"], s=14, alpha=0.55, label=str(gname), edgecolors="none")
            ax.legend(title=group, ncol=min(4, shown["g"].nunique()))
        else:
            ax.scatter(shown["x"], shown["y"], s=14, alpha=0.45, color=palette[0], edgecolors="none")
        slope = intercept = None
        if len(frame) > 2 and frame["x"].std() > 0:
            slope, intercept = np.polyfit(frame["x"], frame["y"], 1)
            xx = np.linspace(frame["x"].min(), frame["x"].max(), 50)
            ax.plot(xx, slope * xx + intercept, color=palette[2], linewidth=2, label="linear trend")
        ax.set_xlabel(x); ax.set_ylabel(y)
        data.update({"points_total": int(len(frame)), "points_shown": int(len(shown)), "pearson_r": corr, "slope": None if slope is None else float(slope)})
        title = title or f"{y} against {x}"
        strength = "" if corr is None else ("very weak" if abs(corr) < 0.1 else "weak" if abs(corr) < 0.3 else "moderate" if abs(corr) < 0.5 else "strong")
        subtitle = subtitle or f"{len(shown):,} of {len(frame):,} points" + (f" · r = {corr:+.2f} ({strength}) · association, not causation" if corr is not None else "")
        caption = f"Each dot is one row ({x} across, {y} up). " + (f"The fitted line is the linear trend: {_fmt(float(slope))} change in {y} per unit of {x}; r = {corr:+.2f} is a {strength} {'positive' if corr > 0 else 'negative'} association. This does not show that {x} causes {y}." if corr is not None and slope is not None else "")

    elif kind == "box":
        if not y:
            raise InvalidArguments("box needs y (numeric) and x (groups)")
        yv = pd.to_numeric(_col(df, y), errors="coerce")
        frame = pd.DataFrame({"x": xs.astype(str).where(xs.notna(), "(missing)"), "y": yv}).dropna()
        keep = frame.groupby("x")["y"].count().sort_values(ascending=False).head(top).index
        frame = frame[frame["x"].isin(keep)]
        order = frame.groupby("x")["y"].median().sort_values(ascending=False).index
        groups = [frame.loc[frame["x"] == g, "y"].values for g in order]
        bp = ax.boxplot(groups, tick_labels=[str(g) for g in order], patch_artist=True, showfliers=True, flierprops={"marker": ".", "markersize": 3, "alpha": 0.4, "markeredgecolor": muted}, medianprops={"color": palette[0], "linewidth": 2})
        for patch in bp["boxes"]:
            patch.set_facecolor(palette[1]); patch.set_edgecolor(palette[0])
        plt.setp(ax.get_xticklabels(), rotation=0 if len(order) <= 6 else 30, ha="right" if len(order) > 6 else "center")
        ax.set_ylabel(y); ax.set_xlabel(x)
        meds = {str(g): float(np.median(v)) for g, v in zip(order, groups)}
        data.update({"labels": list(meds), "medians": list(meds.values()), "n_per_group": [int(len(v)) for v in groups]})
        title = title or f"{y} by {x}"
        subtitle = subtitle or f"boxes show the middle half of values, the line the median · {len(frame):,} rows"
        caption = f"Each box is the middle 50% of {y} for one {x}; the line inside is the median and dots are outliers. {list(meds)[0]} has the highest median ({_fmt(list(meds.values())[0])}), {list(meds)[-1]} the lowest ({_fmt(list(meds.values())[-1])})."

    elif kind == "heatmap":
        if not y:
            raise InvalidArguments("heatmap needs x and y (two categorical columns); optional group = numeric column to aggregate")
        xa = xs.astype(str).where(xs.notna(), "(missing)"); ya = _col(df, y).astype(str).where(_col(df, y).notna(), "(missing)")
        if group:
            zv = pd.to_numeric(_col(df, group), errors="coerce")
            pv = pd.DataFrame({"x": xa, "y": ya, "z": zv}).pivot_table(index="y", columns="x", values="z", aggfunc=agg)
            label = f"{agg} of {group}"
        else:
            pv = pd.crosstab(ya, xa)
            label = "rows"
        if pv.shape[0] > 20:
            pv = pv.loc[pv.sum(axis=1).sort_values(ascending=False).index[:20]]
        if pv.shape[1] > 20:
            pv = pv[pv.sum().sort_values(ascending=False).index[:20]]
        import matplotlib.colors as mcolors
        cmap = mcolors.LinearSegmentedColormap.from_list("squipi", ["#ffffff", palette[1], palette[0]])
        im = ax.imshow(pv.values.astype(float), cmap=cmap, aspect="auto")
        ax.set_xticks(range(pv.shape[1])); ax.set_xticklabels([str(c)[:16] for c in pv.columns], rotation=35, ha="right")
        ax.set_yticks(range(pv.shape[0])); ax.set_yticklabels([str(i)[:20] for i in pv.index])
        ax.grid(False)
        if pv.shape[0] * pv.shape[1] <= 200:
            vmax = np.nanmax(pv.values.astype(float)) if pv.size else 1
            for i in range(pv.shape[0]):
                for j in range(pv.shape[1]):
                    v = pv.values[i, j]
                    if not (isinstance(v, float) and math.isnan(v)):
                        ax.text(j, i, _fmt(float(v)), ha="center", va="center", fontsize=8, color="white" if v > 0.6 * vmax else ink)
        fig.colorbar(im, ax=ax, label=label, fraction=0.035)
        ax.set_xlabel(x); ax.set_ylabel(y)
        data.update({"rows": [str(i) for i in pv.index], "columns": [str(c) for c in pv.columns], "values": [[None if (isinstance(v, float) and math.isnan(v)) else float(v) for v in r] for r in pv.values]})
        title = title or f"{label} by {y} and {x}"
        subtitle = subtitle or f"{pv.shape[0]} × {pv.shape[1]} cells"
        flat = pv.stack()
        if len(flat):
            (yi, xi), top_v = flat.idxmax(), float(flat.max())
            caption = f"Darker cells mean more {label}. The largest cell is {y} = {yi}, {x} = {xi} at {_fmt(top_v)}."
    else:
        raise InvalidArguments(f"unknown chart kind {kind}; use one of {KINDS}")

    svg_path, png_path = _finish(plt, fig, ax, title, subtitle or "", note, out, stem, style)
    return {"chart_path": svg_path, "png_path": png_path, "title": title, "subtitle": subtitle, "caption": caption, "data": data, "style_applied": style}


@tool("create_chart",
      "Draw one chart from a dataset, saved as SVG and PNG with a caption. Kinds: histogram (x numeric); bar (counts of x, or agg of y by x); grouped_bar / stacked_bar (y by x split by group); "
      "line / area (y over x, x a date or ordered value; group = one line per category); scatter (x vs y with a trend line and r; group colours points); box (y by x groups); "
      "heatmap (x and y categorical, counts, or agg of the numeric column named in group). Returns the plotted numbers too.",
      {"type": "object", "properties": {
          "dataset_id": {"type": "string"},
          "style": STYLE_SCHEMA,
          "kind": {"type": "string", "enum": KINDS},
          "x": {"type": "string", "description": "column on the x axis (or the category for bar/box)"},
          "y": {"type": ["string", "null"], "description": "numeric column for line/scatter/box, or to aggregate for bar; second categorical for heatmap"},
          "group": {"type": ["string", "null"], "description": "split by this column (grouped/stacked bar, multi-line, coloured scatter); numeric column to aggregate for heatmap"},
          "agg": {"type": "string", "enum": ["count", "sum", "mean", "median", "min", "max"], "description": "aggregation when y is given (default mean)"},
          "bins": {"type": "integer", "minimum": 3, "maximum": 100},
          "top": {"type": "integer", "minimum": 2, "maximum": 50, "description": "keep the top N categories (default 12)"},
          "horizontal": {"type": ["boolean", "null"]},
          "title": {"type": ["string", "null"]},
          "subtitle": {"type": ["string", "null"]},
          "note": {"type": ["string", "null"], "description": "small print under the chart, e.g. the source"}},
       "required": ["dataset_id", "kind", "x"], "additionalProperties": False},
      examples=[{"dataset_id": "ds_1a2b3c4d", "kind": "bar", "x": "region", "y": "income", "agg": "mean"}])
def create_chart(ctx: JobContext, args: Dict[str, Any]) -> Dict[str, Any]:
    style = normalize_style(args.get("style"))
    ds = ctx.project.dataset(args["dataset_id"])
    ctx.project.allow.check_readable(ds.path)
    ctx.progress(5, "reading")
    sampled = (ds.rows or 0) > MAX_FULL_ROWS
    df = read_dataset(ds, nrows=SAMPLE_ROWS if sampled else None)
    ctx.progress(40, "drawing")
    out = ctx.project.new_artifact_dir("chart", f"{args['kind']}-{args['x']}")
    res = draw(out, args["kind"], df, args["x"], args.get("y"), args.get("group"), args.get("agg") or "mean", args.get("bins"), int(args.get("top") or 12),
               args.get("title"), args.get("subtitle"), args.get("note") or f"Source: {ds.name}" + (" (sampled)" if sampled else ""), args.get("horizontal"), style=style)
    res["data"]["sampled"] = sampled
    ctx.project.write_json(out / "data.json", res["data"])
    ctx.project.audit({"tool": "create_chart", "dataset": ds.id, "kind": args["kind"], "artifact": str(out)})
    ctx.progress(100, "done")
    res.update({"data_path": str(out / "data.json"), "artifact_dir": str(out), "summary_text": res["caption"] or res["title"]})
    return res
