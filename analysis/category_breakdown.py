#!/usr/bin/env python3
"""Per-category Pass@1 breakdown for OmicOS-BixBench results.

  python3 analysis/category_breakdown.py
  # -> analysis/category_breakdown.html  (interactive Plotly bar chart)
  # -> analysis/category_breakdown.png   (static PNG)
  # -> analysis/category_summary.csv     (per-category counts + Pass@1)

BixBench questions carry one or more category tags from a fixed
~14-class taxonomy (RNA-seq, Phylogenetics, Variant Analysis, …).
This script tallies per-category Pass@1 across every graded cell in
`results/*/`, deduplicating by question_id (latest grade wins so
documented rerun fixes supersede the initial sweep).
"""
import argparse
import csv
import glob
import json
import os
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.graph_objects as go

PROJECT = Path(__file__).resolve().parents[1]
ANALYSIS = Path(__file__).resolve().parent


def load_latest_per_question():
    """{question_id: grade.json dict}, picking the most recent file per qid."""
    latest = {}
    for f in glob.glob(str(PROJECT / "results" / "*" / "*" / "*" / "grade.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        qid = d.get("question_id") or Path(f).parent.name
        mtime = os.path.getmtime(f)
        if qid not in latest or mtime > latest[qid][0]:
            latest[qid] = (mtime, d)
    return {qid: d for qid, (_, d) in latest.items()}


def aggregate(grades):
    by_cat = defaultdict(lambda: [0, 0])   # [correct, total]
    n_overall = len(grades)
    n_correct = sum(1 for d in grades.values() if d.get("correct"))
    for d in grades.values():
        ok = bool(d.get("correct"))
        for c in d.get("categories", []) or []:
            by_cat[c][1] += 1
            if ok:
                by_cat[c][0] += 1
    return n_overall, n_correct, by_cat


def write_csv(by_cat, out):
    rows = sorted(
        ((c, *v, 100 * v[0] / v[1] if v[1] else 0)
         for c, v in by_cat.items()),
        key=lambda r: -r[2],
    )
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["category", "correct", "total", "pass_at_1_pct"])
        w.writerows([[c, k, t, f"{p:.1f}"] for c, k, t, p in rows])


def plot_png(by_cat, n_overall, n_correct, out):
    rows = sorted(
        ((c, *v, 100 * v[0] / v[1] if v[1] else 0)
         for c, v in by_cat.items()),
        key=lambda r: r[2],
    )
    cats = [r[0] for r in rows]
    pass_pct = [r[3] for r in rows]
    totals = [r[2] for r in rows]
    correct = [r[1] for r in rows]

    fig, ax = plt.subplots(figsize=(10, max(4, 0.45 * len(cats) + 1.5)))
    colors = ["#2BA66B" if p >= 80 else "#E0A040" if p >= 60 else "#C0392B"
              for p in pass_pct]
    bars = ax.barh(cats, pass_pct, color=colors, edgecolor="white", linewidth=0.6)
    for bar, k, t in zip(bars, correct, totals):
        ax.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height() / 2,
                f"{k}/{t}", va="center", fontsize=8.5, color="#333")
    ax.set_xlim(0, 108)
    ax.set_xlabel("Pass@1 (%)", fontsize=11)
    ax.set_title(f"BixBench-Verified-50 — Pass@1 by category"
                 f"  (overall {n_correct}/{n_overall} = {100*n_correct/n_overall:.1f}%)",
                 fontsize=12, fontweight="bold")
    ax.grid(True, axis="x", ls=":", alpha=0.45)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out, dpi=170, bbox_inches="tight")


def plot_html(by_cat, n_overall, n_correct, out):
    rows = sorted(
        ((c, *v, 100 * v[0] / v[1] if v[1] else 0)
         for c, v in by_cat.items()),
        key=lambda r: r[3],
    )
    cats = [r[0] for r in rows]
    pass_pct = [r[3] for r in rows]
    totals = [r[2] for r in rows]
    correct = [r[1] for r in rows]
    colors = ["#2BA66B" if p >= 80 else "#E0A040" if p >= 60 else "#C0392B"
              for p in pass_pct]

    fig = go.Figure(go.Bar(
        x=pass_pct, y=cats, orientation="h",
        marker_color=colors,
        marker_line=dict(color="white", width=0.6),
        text=[f"{k}/{t}" for k, t in zip(correct, totals)],
        textposition="outside",
        textfont=dict(size=10, color="#333"),
        hovertemplate=("<b>%{y}</b><br>"
                       "Pass@1: %{x:.1f}%<br>"
                       "%{text}<extra></extra>"),
    ))
    fig.update_xaxes(range=[0, 110], title="Pass@1 (%)",
                     gridcolor="#e0e0e0", showline=True, linecolor="#888",
                     mirror=True)
    fig.update_yaxes(gridcolor="white", showline=True, linecolor="#888",
                     mirror=True)
    fig.update_layout(
        title=dict(
            text=(f"<b>BixBench-Verified-50 — Pass@1 by category</b>"
                  f"<br><span style='font-size:13px;color:#777'>"
                  f"overall {n_correct}/{n_overall} = "
                  f"{100*n_correct/n_overall:.1f}% across deduplicated "
                  f"question_ids; latest grade per question wins"
                  f"</span>"),
            x=0.5, xanchor="center", y=0.97,
        ),
        template="simple_white",
        font=dict(family="IBM Plex Sans, Inter, Helvetica, Arial, sans-serif",
                  size=12, color="#222"),
        width=950, height=max(380, 32 * len(cats) + 150),
        margin=dict(l=30, r=40, t=110, b=60),
        plot_bgcolor="white",
        hoverlabel=dict(bgcolor="white", font=dict(size=12), bordercolor="#888"),
    )
    fig.write_html(str(out), include_plotlyjs="cdn", full_html=True,
                   config={"displaylogo": False, "responsive": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=str(ANALYSIS / "category_breakdown.html"))
    ap.add_argument("--png",  default=str(ANALYSIS / "category_breakdown.png"))
    ap.add_argument("--csv",  default=str(ANALYSIS / "category_summary.csv"))
    args = ap.parse_args()

    grades = load_latest_per_question()
    n_overall, n_correct, by_cat = aggregate(grades)
    print(f"unique question_ids: {n_overall}; correct: {n_correct} "
          f"({100*n_correct/n_overall:.1f}%)")

    write_csv(by_cat, args.csv)
    plot_png(by_cat, n_overall, n_correct, args.png)
    plot_html(by_cat, n_overall, n_correct, args.html)
    print(f"saved {args.csv}\nsaved {args.png}\nsaved {args.html}")


if __name__ == "__main__":
    main()
