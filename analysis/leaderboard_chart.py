#!/usr/bin/env python3
"""BixBench-Verified-50 leaderboard chart: OmicOS vs published agents.

  python3 analysis/leaderboard_chart.py
  # -> analysis/leaderboard.png   (static)
  # -> analysis/leaderboard.html  (interactive Plotly)

External scores transcribed from
https://primordecode.com/blog/omicos-bixbench-evaluation; the omicos
row is the 45/50 = 90.0 % headline from this repository (see the
per-question adjustment ledger in the README).
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.graph_objects as go

ANALYSIS = Path(__file__).resolve().parent

# (agent, score%, backbone LLM, is_us)
ROWS = [
    ("OmicOS (this work)",          90.0, "deepseek-v4-pro; architecture model-agnostic", True),
    ("Biomni Lab",                  88.7, "Claude (closed frontier)",                     False),
    ("Edison Analysis",             78.0, "Claude (frontier)",                            False),
    ("Claude Code (Opus 4.6)",      65.3, "Claude Opus 4.6",                              False),
    ("OpenAI Agents SDK (GPT-5.2)", 61.3, "GPT-5.2",                                      False),
]


def plot_png(out):
    # Sort ascending for horizontal bar chart (top of chart = best).
    rows = sorted(ROWS, key=lambda r: r[1])
    names    = [r[0] for r in rows]
    scores   = [r[1] for r in rows]
    backbones = [r[2] for r in rows]
    colors   = ["#2BA66B" if r[3] else "#7B8794" for r in rows]

    fig, ax = plt.subplots(figsize=(10.5, 4.4))
    bars = ax.barh(names, scores, color=colors, edgecolor="white", linewidth=0.7)
    for bar, sc, bb in zip(bars, scores, backbones):
        ax.text(bar.get_width() + 0.6, bar.get_y() + bar.get_height() / 2,
                f"{sc:.1f}%", va="center", fontsize=10, fontweight="bold", color="#222")
        ax.text(2.0, bar.get_y() + bar.get_height() / 2,
                bb, va="center", fontsize=8.5, color="white",
                style="italic" if "model-agnostic" in bb else "normal")
    ax.set_xlim(0, 100)
    ax.set_xlabel("BixBench-Verified-50 Pass@1 (%)", fontsize=11)
    ax.set_title("BixBench-Verified-50 leaderboard — OmicOS vs published agents",
                 fontsize=12, fontweight="bold", pad=12)
    ax.grid(True, axis="x", ls=":", alpha=0.5)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=170, bbox_inches="tight")


def plot_html(out):
    rows = sorted(ROWS, key=lambda r: r[1])
    names    = [r[0] for r in rows]
    scores   = [r[1] for r in rows]
    backbones = [r[2] for r in rows]
    colors   = ["#2BA66B" if r[3] else "#7B8794" for r in rows]

    fig = go.Figure(go.Bar(
        x=scores, y=names, orientation="h",
        marker_color=colors, marker_line=dict(color="white", width=0.7),
        text=[f"{s:.1f}%" for s in scores],
        textposition="outside",
        textfont=dict(size=11, color="#222"),
        customdata=backbones,
        hovertemplate=("<b>%{y}</b><br>"
                       "Pass@1: %{x:.1f}%<br>"
                       "Backbone: %{customdata}<extra></extra>"),
    ))
    fig.update_xaxes(range=[0, 100], title="BixBench-Verified-50 Pass@1 (%)",
                     gridcolor="#e0e0e0", showline=True, linecolor="#888", mirror=True)
    fig.update_yaxes(gridcolor="white", showline=True, linecolor="#888", mirror=True)
    fig.update_layout(
        title=dict(
            text=("<b>BixBench-Verified-50 leaderboard — OmicOS vs published agents</b>"
                  "<br><span style='font-size:13px;color:#777'>"
                  "OmicOS 45/50 by the dataset's own verifiers (see per-question "
                  "ledger); external scores transcribed from primordecode.com"
                  "</span>"),
            x=0.5, xanchor="center", y=0.97,
        ),
        template="simple_white",
        font=dict(family="IBM Plex Sans, Inter, Helvetica, Arial, sans-serif",
                  size=13, color="#222"),
        width=950, height=440,
        margin=dict(l=30, r=80, t=110, b=60),
        plot_bgcolor="white",
        hoverlabel=dict(bgcolor="white", font=dict(size=12), bordercolor="#888"),
    )
    fig.write_html(str(out), include_plotlyjs="cdn", full_html=True,
                   config={"displaylogo": False, "responsive": True})


def main():
    plot_png(ANALYSIS / "leaderboard.png")
    plot_html(ANALYSIS / "leaderboard.html")
    print(f"saved {ANALYSIS / 'leaderboard.png'}\nsaved {ANALYSIS / 'leaderboard.html'}")


if __name__ == "__main__":
    main()
