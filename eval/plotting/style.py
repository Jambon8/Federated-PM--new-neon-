"""Shared thesis-quality matplotlib configuration."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

THESIS_STYLE = {
    "figure.figsize": (6.4, 4.0),
    "font.size": 11,
    "font.family": "serif",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.format": "pdf",
    "axes.spines.top": False,
    "axes.spines.right": False,
}

COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2"]


def apply_style():
    plt.rcParams.update(THESIS_STYLE)


def get_colors(n):
    return COLORS[:n]
