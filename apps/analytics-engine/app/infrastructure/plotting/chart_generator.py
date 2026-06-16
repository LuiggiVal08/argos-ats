"""matplotlib chart generators for validation reports."""
from __future__ import annotations

import io
from typing import Any

import numpy as np

HAS_MPL = False
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    plt = None  # type: ignore


def _check_mpl() -> None:
    if not HAS_MPL:
        raise RuntimeError(
            "matplotlib is required for chart generation. "
            "Install it with: pip install matplotlib"
        )


def plot_confusion_matrix(
    matrix: list[list[int]],
    labels: list[str],
    title: str = "Confusion Matrix",
    cmap: str = "Blues",
    figsize: tuple[int, int] = (8, 6),
    dpi: int = 120,
) -> bytes:
    _check_mpl()
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    arr = np.array(matrix)
    im = ax.imshow(arr, interpolation="nearest", cmap=cmap)
    ax.figure.colorbar(im, ax=ax)
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_yticklabels(labels)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title(title)
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(j, i, str(arr[i, j]), ha="center", va="center")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue()


def plot_probability_histogram(
    probabilities: np.ndarray,
    n_bins: int = 20,
    title: str = "Predicted Probability Distribution",
    figsize: tuple[int, int] = (10, 6),
    dpi: int = 120,
) -> bytes:
    _check_mpl()
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    colors = ["#2ecc71", "#e74c3c", "#95a5a6"]
    for i, label in enumerate(["BUY", "SELL", "HOLD"]):
        ax.hist(
            probabilities[:, i], bins=n_bins, alpha=0.6, label=label,
            color=colors[i], density=True,
        )
    ax.set_xlabel("Probability")
    ax.set_ylabel("Density")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue()


def plot_calibration_curve(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    n_bins: int = 10,
    title: str = "Calibration Curve",
    figsize: tuple[int, int] = (8, 6),
    dpi: int = 120,
) -> bytes:
    _check_mpl()
    try:
        from sklearn.calibration import calibration_curve
    except ImportError:
        raise RuntimeError("scikit-learn required for calibration curve")

    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    n_classes = y_prob.shape[1]
    colors = ["#2ecc71", "#e74c3c", "#95a5a6"]
    labels = ["BUY", "SELL", "HOLD"]
    for i in range(n_classes):
        y_bin = (y_true == i).astype(int)
        prob_true, prob_pred = calibration_curve(
            y_bin, y_prob[:, i], n_bins=n_bins, strategy="uniform"
        )
        ax.plot(prob_pred, prob_true, "o-", label=labels[i], color=colors[i])
    ax.plot([0, 1], [0, 1], "k--", label="Perfect", alpha=0.5)
    ax.set_xlabel("Mean Predicted Probability")
    ax.set_ylabel("Fraction of Positives")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue()


def plot_feature_importance(
    ranking: list[tuple[str, float]],
    top_n: int = 20,
    title: str = "MetaModel Feature Importance",
    figsize: tuple[int, int] = (10, 8),
    dpi: int = 120,
) -> bytes:
    _check_mpl()
    names = [r[0] for r in ranking[:top_n]]
    scores = [r[1] for r in ranking[:top_n]]
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    ax.barh(range(len(names)), scores, align="center")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names)
    ax.invert_yaxis()
    ax.set_xlabel("Importance")
    ax.set_title(title)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue()


def plot_uncertainty_distribution(
    uncertainties: np.ndarray,
    n_bins: int = 50,
    title: str = "MC Dropout Uncertainty Distribution",
    figsize: tuple[int, int] = (10, 6),
    dpi: int = 120,
) -> bytes:
    _check_mpl()
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    ax.hist(uncertainties, bins=n_bins, alpha=0.7, color="#3498db", density=True)
    ax.axvline(
        np.mean(uncertainties), color="#e74c3c", linestyle="--",
        label=f"Mean: {np.mean(uncertainties):.4f}",
    )
    ax.axvline(
        np.median(uncertainties), color="#f39c12", linestyle="--",
        label=f"Median: {np.median(uncertainties):.4f}",
    )
    ax.axvline(
        np.percentile(uncertainties, 95), color="#9b59b6", linestyle=":",
        label=f"P95: {np.percentile(uncertainties, 95):.4f}",
    )
    ax.set_xlabel("Prediction Std")
    ax.set_ylabel("Density")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    plt.close(fig)
    return buf.getvalue()
