"""Probability calibrator using Platt Scaling and Isotonic Regression.

Wraps sklearn's CalibratedClassifierCV for multi-class calibration.

Methods:
    Platt Scaling: parametric (logistic regression) — method="sigmoid".
    Isotonic Regression: non-parametric — method="isotonic".

Usage:
    calibrator = SklearnProbabilityCalibrator(method="sigmoid")
    await calibrator.fit(val_probs, val_labels)
    calibrated = await calibrator.calibrate(raw_probs)

Safe serialization (no pickle):
    data = calibrator.to_safe_dict()
    calibrator2 = SklearnProbabilityCalibrator(method=data["method"])
    calibrator2.from_safe_dict(data)
"""
from __future__ import annotations

from typing import Any, Literal

import numpy as np
import structlog

from ...application.ports.probability_calibrator import (
    CalibrationError,
    ProbabilityCalibrator,
)

log = structlog.get_logger()

try:
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    _SKLEARN_AVAILABLE = True
except ImportError:
    _SKLEARN_AVAILABLE = False


CalibrationMethod = Literal["sigmoid", "isotonic"]


class SklearnProbabilityCalibrator:
    """Multi-class probability calibrator using sklearn.

    For multi-class, fits one calibrator per class in a
    one-vs-rest fashion. Supports safe JSON serialization
    via to_safe_dict() / from_safe_dict() — no pickle.

    Args:
        method: "sigmoid" for Platt Scaling, "isotonic" for Isotonic Regression.
    """

    def __init__(self, method: CalibrationMethod = "sigmoid") -> None:
        if not _SKLEARN_AVAILABLE:
            raise RuntimeError(
                "sklearn is required for SklearnProbabilityCalibrator. "
                "Install it via: pip install scikit-learn>=1.3"
            )

        if method not in ("sigmoid", "isotonic"):
            raise ValueError(f"method must be 'sigmoid' or 'isotonic', got {method!r}")

        self._method = method
        self._calibrators: list[LogisticRegression | IsotonicRegression] | None = None
        self._scaler: StandardScaler | None = None
        self._fitted: bool = False

    async def fit(
        self,
        probabilities: np.ndarray,
        targets: np.ndarray,
    ) -> None:
        """Fit one-vs-rest calibrators.

        Args:
            probabilities: (n_samples, n_classes) raw probabilities.
            targets: (n_samples,) integer class labels (0, 1, 2).

        Raises CalibrationError if fitting fails.
        """
        try:
            n_classes = probabilities.shape[1]
            self._calibrators = []
            self._scaler = StandardScaler()

            # Scale logits for numerical stability
            logits = np.log(probabilities + 1e-8)
            logits_scaled = self._scaler.fit_transform(logits)

            for c in range(n_classes):
                binary_target = (targets == c).astype(np.float64)

                if self._method == "sigmoid":
                    cal = LogisticRegression(
                        solver="lbfgs",
                        max_iter=1000,
                        random_state=42,
                    )
                else:
                    cal = IsotonicRegression(
                        out_of_bounds="clip",
                        increasing=True,
                    )

                cal.fit(logits_scaled[:, c:c+1], binary_target)
                self._calibrators.append(cal)

            self._fitted = True
            log.info(
                "calibrator_fitted",
                method=self._method,
                n_classes=n_classes,
            )

        except Exception as e:
            raise CalibrationError(f"calibrator_fit_failed: {e}") from e

    async def calibrate(
        self,
        probabilities: np.ndarray,
    ) -> np.ndarray:
        """Calibrate probabilities using fitted calibrators.

        Args:
            probabilities: (n_classes,) raw model probabilities.

        Returns:
            (n_classes,) calibrated probabilities.

        Raises CalibrationError if not fitted or inference fails.
        """
        if not self._fitted or self._calibrators is None or self._scaler is None:
            # Degraded mode: return raw probabilities
            log.warning("calibrator_not_fitted_returning_raw")
            return probabilities

        try:
            probs_2d = probabilities.reshape(1, -1)
            logits = np.log(probs_2d + 1e-8)
            logits_scaled = self._scaler.transform(logits)

            calibrated = []
            for c, cal in enumerate(self._calibrators):
                cal_prob = cal.predict(logits_scaled[:, c:c+1])[0]
                calibrated.append(float(np.clip(cal_prob, 0.01, 0.99)))

            calibrated = np.array(calibrated)
            calibrated /= calibrated.sum()  # renormalize

            return calibrated

        except Exception as e:
            raise CalibrationError(f"calibrator_inference_failed: {e}") from e

    def to_safe_dict(self) -> dict[str, Any]:
        """Serialize calibrator state to a JSON-safe dict (no pickle).

        Returns:
            dict with method, fitted flag, calibrator params, and scaler stats.
            All numpy arrays are converted to lists.
        """
        calibrators_list: list[dict[str, Any]] = []
        if self._calibrators is not None:
            for c in self._calibrators:
                if isinstance(c, LogisticRegression):
                    calibrators_list.append({
                        "type": "LogisticRegression",
                        "coef_": c.coef_.tolist(),
                        "intercept_": c.intercept_.tolist(),
                        "classes_": c.classes_.tolist() if hasattr(c, "classes_") else [],
                    })
                elif isinstance(c, IsotonicRegression):
                    calibrators_list.append({
                        "type": "IsotonicRegression",
                        "X_thresholds_": c.X_thresholds_.tolist(),
                        "y_thresholds_": c.y_thresholds_.tolist(),
                    })

        scaler_dict: dict[str, Any] = {}
        if self._scaler is not None:
            scaler_dict = {
                "mean_": self._scaler.mean_.tolist(),
                "var_": self._scaler.var_.tolist(),
                "n_features_in_": int(self._scaler.n_features_in_),
            }

        return {
            "version": 1,
            "method": self._method,
            "fitted": self._fitted,
            "calibrators": calibrators_list,
            "scaler": scaler_dict,
        }

    def from_safe_dict(self, data: dict[str, Any]) -> None:
        """Restore calibrator state from a dict produced by to_safe_dict().

        Args:
            data: dict with calibrator state (version, method, fitted,
                  calibrators, scaler).

        Raises:
            CalibrationError if the dict is malformed.
        """
        if data.get("version") != 1:
            raise CalibrationError(f"unsupported calibrator version: {data.get('version')}")

        self._method = data.get("method", "sigmoid")
        self._fitted = data.get("fitted", False)

        self._calibrators = []
        for cal_data in data.get("calibrators", []):
            cal_type = cal_data.get("type")
            if cal_type == "LogisticRegression":
                cal = LogisticRegression()
                cal.coef_ = np.array(cal_data["coef_"])
                cal.intercept_ = np.array(cal_data["intercept_"])
                classes = cal_data.get("classes_", [])
                cal.classes_ = np.array(classes) if classes else np.array([0, 1])
                nf = cal.coef_.shape[1]
                cal.n_features_in_ = nf
                self._calibrators.append(cal)

            elif cal_type == "IsotonicRegression":
                cal = IsotonicRegression()
                cal.X_thresholds_ = np.array(cal_data["X_thresholds_"])
                cal.y_thresholds_ = np.array(cal_data["y_thresholds_"])
                cal.f_ = None
                cal.increasing_ = True
                self._calibrators.append(cal)

        scaler_data = data.get("scaler", {})
        if scaler_data:
            scaler = StandardScaler()
            scaler.mean_ = np.array(scaler_data["mean_"])
            scaler.var_ = np.array(scaler_data["var_"])
            scaler.scale_ = np.sqrt(scaler.var_)
            scaler.n_features_in_ = int(scaler_data.get("n_features_in_", len(scaler.mean_)))
            self._scaler = scaler
