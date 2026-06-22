"""FASE 5.5.1 — Feature Selection.

Reduce 68 features → ~20-30 using:
1. Mutual Information ranking
2. Correlation filter (never delete solely on corr)
3. VIF elimination
4. RandomForest Feature Importance
5. SHAP (TreeExplainer, top 20)
6. Recursive Feature Elimination (RFE)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_selection import mutual_info_classif, RFE
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "app"))
from domain.entities.multi_timeframe_aligner import MultiTimeframeAligner

OHLCV_CACHE = BASE_DIR / "data" / "btc_usdt_1h.parquet"
FUNDING_CACHE = BASE_DIR / "data" / "btc_funding_rates.parquet"
OI_CACHE = BASE_DIR / "data" / "btc_open_interest.parquet"
REPORT_DIR = BASE_DIR / "reports" / "feature_selection"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

BASE_FEATURES = (
    "open", "high", "low", "close", "volume",
    "rsi", "ema_fast", "ema_medium", "ema_slow",
    "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower",
    "atr", "adx", "obv", "volume_sma", "pct_change",
)
MTF_FEATURES = MultiTimeframeAligner.expected_feature_names(higher_tfs=("4h", "1d"))
AUX_FEATURES = ("funding_rate", "funding_momentum", "oi_change_pct")
ENG_FEATURES = (
    "log_return_1", "log_return_3", "log_return_6",
    "close_lag_1", "close_lag_3", "close_lag_6",
    "rolling_std_6", "rolling_std_12", "rolling_std_24",
    "zscore_close", "zscore_volume",
    "roc_3", "roc_6",
    "trend_regime", "volatility_regime",
)
ALL_NAMES = BASE_FEATURES + MTF_FEATURES + AUX_FEATURES + ENG_FEATURES


def load_data():
    import asyncio
    df = pd.read_parquet(OHLCV_CACHE)
    funding_df = pd.read_parquet(FUNDING_CACHE) if FUNDING_CACHE.exists() else None
    oi_df = pd.read_parquet(OI_CACHE) if OI_CACHE.exists() else None

    import ta as ta_lib
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    base = pd.DataFrame({
        "open": df["open"].astype(float), "high": high, "low": low, "close": close, "volume": volume,
        "rsi": ta_lib.momentum.RSIIndicator(close, window=14).rsi(),
        "ema_fast": ta_lib.trend.EMAIndicator(close, window=9).ema_indicator(),
        "ema_medium": ta_lib.trend.EMAIndicator(close, window=21).ema_indicator(),
        "ema_slow": ta_lib.trend.EMAIndicator(close, window=50).ema_indicator(),
    }, columns=BASE_FEATURES)
    macd = ta_lib.trend.MACD(close)
    base["macd"] = macd.macd()
    base["macd_signal"] = macd.macd_signal()
    base["macd_hist"] = macd.macd_diff()
    bb = ta_lib.volatility.BollingerBands(close, window=20, window_dev=2)
    base["bb_upper"] = bb.bollinger_hband()
    base["bb_middle"] = bb.bollinger_mavg()
    base["bb_lower"] = bb.bollinger_lband()
    base["atr"] = ta_lib.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
    base["adx"] = ta_lib.trend.ADXIndicator(high, low, close, window=14).adx()
    base["obv"] = ta_lib.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    base["volume_sma"] = volume.rolling(20).mean()
    base["pct_change"] = close.pct_change() * 100.0
    base = base.bfill().ffill().fillna(0.0)

    # MTF
    ohlcv_list = df.reset_index().to_dict(orient="records")
    mtf_df = MultiTimeframeAligner.compute(ohlcv_list, base_tf="1h", higher_tfs=("4h", "1d"))
    mtf_df = mtf_df[list(MTF_FEATURES)]

    # Aux
    aux = pd.DataFrame(0.0, index=df.index, columns=AUX_FEATURES)
    if funding_df is not None and len(funding_df) > 0:
        fs = funding_df.set_index("timestamp")["fundingRate"].astype(float) * 100.0
        al = fs.reindex(df["timestamp"], method="ffill")
        aux["funding_rate"] = al
        aux["funding_momentum"] = al.diff(3)
    if oi_df is not None and len(oi_df) > 0:
        oc = next((c for c in ["openInterestValue", "openInterest"] if c in oi_df.columns), oi_df.columns[1])
        oi_ts = oi_df.set_index("timestamp")[oc].astype(float)
        ao = oi_ts.reindex(df["timestamp"], method="ffill")
        aux["oi_change_pct"] = ao.pct_change(periods=24) * 100.0
    aux = aux.bfill().ffill().fillna(0.0)

    # Engineered
    eng = pd.DataFrame(index=df.index)
    cv = close.values
    eng["log_return_1"] = np.append([0], np.diff(np.log(cv)))
    eng["log_return_3"] = np.append([0, 0, 0], cv[3:] / cv[:-3] - 1)
    eng["log_return_6"] = np.append(np.zeros(6), cv[6:] / cv[:-6] - 1)
    eng["close_lag_1"] = np.append([cv[0]], cv[:-1])
    eng["close_lag_3"] = np.append(np.zeros(3), cv[:-3])
    eng["close_lag_6"] = np.append(np.zeros(6), cv[:-6])
    ret = eng["log_return_1"].values
    for w in [6, 12, 24]:
        eng[f"rolling_std_{w}"] = pd.Series(ret).rolling(w).std().values
    for name, arr in [("close", cv), ("volume", volume.values)]:
        m = pd.Series(arr).rolling(20).mean().values
        s = pd.Series(arr).rolling(20).std().values
        eng[f"zscore_{name}"] = (arr - m) / np.maximum(s, 1e-10)
    eng["roc_3"] = np.append(np.zeros(3), cv[3:] / cv[:-3] - 1)
    eng["roc_6"] = np.append(np.zeros(6), cv[6:] / cv[:-6] - 1)
    es = pd.Series(cv).ewm(span=9).mean().values
    el = pd.Series(cv).ewm(span=50).mean().values
    eng["trend_regime"] = np.sign(es - el)
    atr_s = pd.Series(cv).rolling(14).std().values
    atr_p = pd.Series(atr_s).rank(pct=True).values
    eng["volatility_regime"] = np.where(atr_p > 0.7, 1, np.where(atr_p < 0.3, -1, 0))
    eng = eng.bfill().ffill().fillna(0.0)

    X = np.column_stack([
        base.values.astype(np.float64),
        mtf_df.values.astype(np.float64),
        aux.values.astype(np.float64),
        eng.values.astype(np.float64),
    ])

    # Labels (framing B)
    future_ret = (close.shift(-5) / close - 1.0) * 100.0
    vol = future_ret.rolling(60).std()
    adj = future_ret / vol.clip(lower=1e-10)
    y = np.full(len(adj), -1, dtype=int)
    y[adj > 0.002] = 1
    y[adj < -0.002] = 0

    return df, X, y, close.values, high.values, low.values, volume.values


def main():
    print("=" * 60)
    print("FASE 5.5.1 — Feature Selection")
    print("=" * 60)

    df, X, y, close, high, low, volume = load_data()
    valid = y != -1
    X_v, y_v = X[valid], y[valid]
    names = list(ALL_NAMES)
    n = X_v.shape[1]
    print(f"Feature matrix: {X_v.shape}")

    scaler = StandardScaler()
    X_s = scaler.fit_transform(X_v)

    results: dict = {}

    # ── 1. Mutual Information ──
    print("\n[1/6] Mutual Information...")
    mi = mutual_info_classif(X_s, y_v, random_state=42)
    mi_ranking = sorted([(names[i], float(mi[i])) for i in range(n)], key=lambda x: -x[1])
    mi_map = {names[i]: float(mi[i]) for i in range(n)}
    results["mutual_information"] = {
        "ranking": mi_ranking,
        "top_10": mi_ranking[:10],
        "zero_mi": [names[i] for i in range(n) if mi[i] < 1e-6],
    }
    print(f"  Features with MI≈0: {len(results['mutual_information']['zero_mi'])}")

    # ── 2. Train RF ──
    print("\n[2/6] RandomForest for feature importance + predictions...")
    rf = RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1)
    rf.fit(X_s, y_v)
    rf_imp = rf.feature_importances_
    rf_ranking = sorted([(names[i], float(rf_imp[i])) for i in range(n)], key=lambda x: -x[1])
    rf_map = {names[i]: float(rf_imp[i]) for i in range(n)}
    results["rf_importance"] = {"ranking": rf_ranking, "top_10": rf_ranking[:10]}

    # ── 3. Correlation filter ──
    print("\n[3/6] Correlation analysis...")
    corr_df = pd.DataFrame(X_s, columns=names)
    corr_mat = corr_df.corr().abs()
    triu = np.triu(np.ones_like(corr_mat), k=1)
    high_corr_pairs = []
    corr_remove_candidates = set()
    for i in range(n):
        for j in range(i + 1, n):
            if triu[i, j]:
                val = float(corr_mat.iloc[i, j])
                if val > 0.85:
                    high_corr_pairs.append((names[i], names[j], val))
                    # Keep the one with higher MI; only mark for removal if MI agrees
                    if mi_map.get(names[i], 0) < mi_map.get(names[j], 0):
                        corr_remove_candidates.add(names[i])
                    else:
                        corr_remove_candidates.add(names[j])
    results["correlation"] = {
        "n_high_corr_pairs": len(high_corr_pairs),
        "top_pairs": high_corr_pairs[:20],
        "remove_candidates": list(corr_remove_candidates),
    }
    print(f"  High corr pairs (>0.85): {len(high_corr_pairs)}")
    print(f"  Correlation removal candidates: {len(corr_remove_candidates)}")

    # ── 4. VIF ──
    print("\n[4/6] VIF analysis...")
    from sklearn.linear_model import LinearRegression
    vif_values = []
    for i in range(n):
        y_i = X_s[:, i]
        X_i = np.delete(X_s, i, axis=1)
        lr = LinearRegression().fit(X_i, y_i)
        r2 = lr.score(X_i, y_i)
        vif = float("inf") if r2 >= 0.999 else float(1.0 / (1.0 - r2))
        vif_values.append((names[i], vif))
    vif_ranking = sorted(vif_values, key=lambda x: -x[1] if x[1] != float("inf") else 1e10)
    vif_high = [n for n, v in vif_values if v > 10 or v == float("inf")]
    results["vif"] = {"ranking": vif_ranking[:20], "high_vif": vif_high}
    print(f"  Features with VIF>10: {len(vif_high)}")

    # ── 5. SHAP (top 20 features) ──
    print("\n[5/6] SHAP (TreeExplainer, top 20 features)...")
    rf_small = RandomForestClassifier(max_depth=5, n_estimators=50, class_weight="balanced", random_state=42, n_jobs=-1)
    rf_small.fit(X_s, y_v)
    import shap
    explainer = shap.TreeExplainer(rf_small)
    sample_idx = np.random.choice(len(X_s), min(2000, len(X_s)), replace=False)
    shap_raw = explainer.shap_values(X_s[sample_idx])
    # shap_raw may be list (older vers) or 3D array (newer vers, shape n×f×c)
    if isinstance(shap_raw, list):
        shap_abs = np.abs(shap_raw[1]).mean(axis=0)  # class 1 (TRADE)
    elif shap_raw.ndim == 3:
        shap_abs = np.abs(shap_raw[:, :, 1]).mean(axis=0)  # newer shap format
    else:
        shap_abs = np.abs(shap_raw).mean(axis=0)
    shap_abs = np.asarray(shap_abs).ravel()
    assert len(shap_abs) == n, f"SHAP output dim mismatch: {len(shap_abs)} vs {n}"
    shap_ranking = sorted([(names[i], float(shap_abs[i])) for i in range(n)], key=lambda x: -x[1])
    shap_map = {names[i]: float(shap_abs[i]) for i in range(n)}
    results["shap"] = {"ranking": shap_ranking, "top_10": shap_ranking[:10]}
    print(f"  SHAP computed on {len(sample_idx)} samples, {n} features")

    # ── 6. RFE ──
    print("\n[6/6] RFE (target 25 features)...")
    rfe_selector = RFE(LogisticRegression(max_iter=2000, random_state=42), n_features_to_select=25)
    rfe_selector.fit(X_s, y_v)
    rfe_selected = [names[i] for i in range(n) if rfe_selector.support_[i]]
    rfe_ranking = sorted([(names[i], float(rfe_selector.ranking_[i])) for i in range(n)], key=lambda x: x[1])
    results["rfe"] = {"selected": rfe_selected, "ranking": rfe_ranking}
    print(f"  RFE selected: {len(rfe_selected)} features")

    # ── CONSENSUS: build final retained set ──
    print("\n[CONSENSUS] Building retained feature set...")

    # Features to remove (must pass multiple filters):
    # a) VIF > 10 AND (low MI OR low SHAP)
    # b) Corr candidate AND MI < median MI AND SHAP < median SHAP
    # c) RF importance near zero
    mi_median = np.median(mi)
    shap_median = np.median(shap_abs)
    rf_median = np.median(rf_imp)

    to_remove = set()
    for name_ in names:
        reasons = []
        # VIF
        vif_val = next((v for n, v in vif_values if n == name_), 0)
        if vif_val > 10 or vif_val == float("inf"):
            reasons.append("VIF>10")
        # Correlation (only remove if also low MI and low SHAP)
        if name_ in corr_remove_candidates:
            if mi_map.get(name_, 1) < mi_median and shap_map.get(name_, 1) < shap_median:
                reasons.append("CORR+MI+SHAP")
        # Near-zero RF importance
        if rf_map.get(name_, 1) < 0.001:
            reasons.append("RF_IMP≈0")
        # Near-zero MI
        if mi_map.get(name_, 1) < 1e-6:
            reasons.append("MI≈0")

        if reasons:
            to_remove.add(name_)

    # Re-evaluate: if MI > 0.01 OR SHAP > 0.01, keep regardless of correlation
    for name_ in list(to_remove):
        if mi_map.get(name_, 0) > 0.01 or shap_map.get(name_, 0) > 0.01:
            if "VIF>10" in [r for r in ["VIF>10", "CORR+MI+SHAP", "RF_IMP≈0", "MI≈0"]]:
                # High signal feature — keep even with VIF>10, but note it
                if mi_map.get(name_, 0) > 0.05:
                    to_remove.discard(name_)

    retained = [n for n in names if n not in to_remove]
    removed = [n for n in names if n in to_remove]

    # RFE override: if a feature was selected by RFE, keep it unless multiple strong reasons
    for name_ in rfe_selected:
        if name_ in to_remove:
            reasons_count = sum([
                name_ in corr_remove_candidates,
                mi_map.get(name_, 0) < 1e-6,
                rf_map.get(name_, 0) < 0.001,
            ])
            if reasons_count < 2:
                to_remove.discard(name_)
                retained.append(name_)
                removed = [n for n in removed if n != name_]
            else:
                print(f"  ⚠ Forcing removal of RFE-selected {name_} ({reasons_count} reasons)")

    # Final sort
    retained_sorted = sorted(retained, key=lambda n: -mi_map.get(n, 0))
    removed_sorted = sorted(removed, key=lambda n: -mi_map.get(n, 0))

    results["consensus"] = {
        "n_original": n,
        "n_retained": len(retained_sorted),
        "n_removed": len(removed_sorted),
        "retained": retained_sorted,
        "removed": removed_sorted,
        "removed_with_reasons": {n: {"MI": mi_map.get(n, 0), "SHAP": shap_map.get(n, 0), "RF_IMP": rf_map.get(n, 0)} for n in removed_sorted},
    }
    print(f"\n  Original: {n} features")
    print(f"  Retained: {len(retained_sorted)} features")
    print(f"  Removed: {len(removed_sorted)} features")
    print(f"\n  Top 10 retained:")
    for feat_name in retained_sorted[:10]:
        print(f"    {feat_name}: MI={mi_map.get(feat_name,0):.4f} SHAP={shap_map.get(feat_name,0):.4f} RFimp={rf_map.get(feat_name,0):.4f}")
    print(f"\n  Top 10 removed:")
    for feat_name in removed_sorted[:10]:
        print(f"    {feat_name}: MI={mi_map.get(feat_name,0):.4f} SHAP={shap_map.get(feat_name,0):.4f} RFimp={rf_map.get(feat_name,0):.4f}")

    # ── VALIDATE: re-evaluate RF on retained set ──
    print("\n[VALIDATION] Re-evaluating RF on retained features...")
    retained_idx = [i for i in range(n) if names[i] in retained_sorted]
    X_r = X_s[:, retained_idx]
    rf_val = RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1)
    rf_val.fit(X_r, y_v)
    from sklearn.metrics import roc_auc_score
    y_prob = rf_val.predict_proba(X_r)[:, 1]
    auc_retained = float(roc_auc_score(y_v, y_prob))
    print(f"  AUC on retained ({X_r.shape[1]} feats): {auc_retained:.4f}")

    # Original AUC (post-sprint best was 0.844)
    rf_orig = RandomForestClassifier(max_depth=7, n_estimators=100, class_weight="balanced", random_state=42, n_jobs=-1)
    rf_orig.fit(X_s, y_v)
    y_prob_o = rf_orig.predict_proba(X_s)[:, 1]
    auc_original = float(roc_auc_score(y_v, y_prob_o))
    print(f"  AUC on original ({n} feats): {auc_original:.4f}")
    print(f"  AUC delta: {auc_retained - auc_original:+.4f}")

    results["validation"] = {
        "auc_retained": auc_retained,
        "auc_original": auc_original,
        "auc_delta": auc_retained - auc_original,
        "pass": (auc_retained >= auc_original - 0.02),
    }

    # ── Write report ──
    report_path = REPORT_DIR / "feature_selection_report.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nReport: {report_path}")

    # Write retained names for downstream scripts
    retained_path = BASE_DIR / "data" / "retained_features.txt"
    with open(retained_path, "w") as f:
        for n in retained_sorted:
            f.write(n + "\n")
    print(f"Retained features: {retained_path}")

    return retained_sorted, retained_idx, names


if __name__ == "__main__":
    retained, idx, names = main()
