# ADR-003: Logistic Regression Model Selection

**Status:** ACCEPTED (2026-06-26)

## Context

ARGOS ATS needs a primary model for directional prediction (BUY/HOLD/SELL). The model must satisfy four requirements: interpretable coefficients, low overfitting risk, fast inference (<100ms), and probabilistic output. 52 model configurations × 10 walk-forward folds were evaluated.

## Options Considered

- **GradientBoosting** — MCC 0.3102; strong but opaque and slower to train.
- **RandomForest** — MCC 0.2374; significantly lower performance.
- **RidgeClassifier** — MCC 0.2726; competitive but outperformed.
- **LinearSVC** — MCC 0.3304; selected as secondary model.
- **LogisticRegression** — MCC 0.3732; selected as primary model.

## Decision

**LogisticRegression** with `C=10.0`, `solver=lbfgs`, no `class_weight`, and `RobustScaler`. This configuration achieves the highest MCC of 0.3732.

`C=10.0` was preferred over `C=100.0` (0.3732 vs 0.3661) for better generalization — the lower regularization penalty indicates the signal benefits from less aggressive shrinkage, but the gap narrows at higher C values suggesting diminishing returns.

The systematic outperformance of linear models over tree-based models (+0.0427 MCC) confirms that the alpha signal in this feature space is predominantly linear.

## Consequences

**Positive:**
- Interpretable coefficients — each feature's contribution is directly measurable
- Fast inference — O(n_features) complexity with softmax probabilistic output
- Low overfitting risk — linear decision boundary with L2 regularization
- Established baseline for future model iterations

**Negative:**
- Limited to linear decision boundaries
- May miss feature interactions detectable by non-linear models
- Future regime changes requiring non-linear boundaries would necessitate model replacement

## Alternatives Rejected

- **XGBoost / LSTM:** insufficient data volume for deep or ensemble models; risk of severe overfitting dominates.
- **RandomForest:** significantly lower MCC (0.2374) does not justify the loss of interpretability.
- **Reinforcement Learning:** not viable before alpha validation; adds complexity without proven signal.

## References

- `archive/qv2/qv2_phase375_output/PHASE375_REPORT.md`
