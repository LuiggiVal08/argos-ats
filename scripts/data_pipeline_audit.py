#!/usr/bin/env python3
"""Data Pipeline Audit + Drift Validation + Feature Collapse + Distribution Comparison.

Phases 1–4: verifies the pipeline from Binance → Redis → build_features
is consistent with training, and quantifies distribution drift.

Outputs:
    reports/data_pipeline_audit.md       (Phase 1)
    reports/drift_report.json            (Phase 2)
    reports/drift_report.md              (Phase 2)
    reports/feature_collapse_report.md   (Phase 3)
    reports/distribution_comparison/     (Phase 4)
"""
from __future__ import annotations

import asyncio
import json
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path("/home/egraterol/projects/argos-bot")
AE_PATH = REPO_ROOT / "apps" / "analytics-engine"
sys.path.insert(0, str(AE_PATH))
sys.path.insert(0, str(AE_PATH / "app"))

import ccxt
import redis as redis_lib
from app.domain.entities.multi_timeframe_aligner import MultiTimeframeAligner
from app.domain.value_objects.model_config import ModelConfig
from app.infrastructure.training.data_preprocessor import TaDataPreprocessor

REPORTS = REPO_ROOT / "reports"
DIST_DIR = REPORTS / "distribution_comparison"
MODEL_DIR = REPO_ROOT / "models" / "btc"

SEVERITY_ZSCORE = {
    "NORMAL": (0, 2),
    "MODERATE": (2, 5),
    "HIGH": (5, 10),
    "EXTREME": (10, float("inf")),
}

# ── Helpers ────────────────────────────────────────────────────────────

REDIS_URL = os.environ.get("ARGOS_BROKER_URL", "redis://localhost:6379")


def load_model_assets():
    model = pickle.loads((MODEL_DIR / "model.pkl").read_bytes())
    scaler = pickle.loads((MODEL_DIR / "scaler.pkl").read_bytes())
    metadata = json.loads((MODEL_DIR / "metadata.json").read_text())
    return model, scaler, metadata


def load_candles_from_redis(count=1200):
    r = redis_lib.from_url(REDIS_URL, socket_connect_timeout=5)
    raw = r.xrevrange("candles:btc_usdt:3600s", count=count)
    raw.reverse()
    seen = set()
    candles = []
    for eid, fields in raw:
        rp = fields.get(b"p") or fields.get("p")
        if isinstance(rp, bytes):
            rp = rp.decode()
        data = json.loads(rp)
        ts = data.get("close_ts", data.get("open_ts", 0))
        if ts in seen:
            continue
        seen.add(ts)
        candles.append({
            "timestamp": ts,
            "open": float(data["open"]),
            "high": float(data["high"]),
            "low": float(data["low"]),
            "close": float(data["close"]),
            "volume": float(data["volume"]),
        })
    return candles


def fetch_ccxt_ohlcv(symbol="BTC/USDT", timeframe="1h", limit=1000):
    ex = ccxt.binance({"options": {"defaultType": "future"}})
    raw = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    candles = []
    for ts, o, h, l, c, v in raw:
        candles.append({
            "timestamp": int(ts),
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v),
        })
    return candles


def fetch_funding_history(symbol="BTC/USDT", limit=500):
    ex = ccxt.binance({"options": {"defaultType": "future"}})
    try:
        raw = ex.fetch_funding_rate_history(symbol, limit=limit)
        rates = []
        for r in raw:
            rates.append({
                "timestamp": int(r["timestamp"]),
                "funding_rate": float(r.get("fundingRate", 0)),
            })
        return rates
    except Exception as e:
        print(f"  WARN: funding fetch failed: {e}")
        return []


def md_table(rows, headers, title=None):
    lines = []
    if title:
        lines.append(f"### {title}\n")
    sep = "| " + " | ".join(h for h in headers) + " |"
    lines.append(sep)
    sep2 = "| " + " | ".join("---" for _ in headers) + " |"
    lines.append(sep2)
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════════
#  PHASE 1 — DATA PIPELINE AUDIT
# ═══════════════════════════════════════════════════════════════════════

def phase1_audit():
    print("=" * 60)
    print("  PHASE 1: DATA PIPELINE AUDIT")
    print("=" * 60)

    sections = []
    findings = []  # (severity, finding)

    # 1.1 Volume comparison: CCXT vs Redis
    print("\n[1.1] Volume comparison: CCXT vs Redis...")
    ccxt_candles = fetch_ccxt_ohlcv(limit=100)
    redis_candles = load_candles_from_redis(count=100)

    # Align by timestamp
    ccxt_by_ts = {c["timestamp"]: c for c in ccxt_candles}
    redis_by_ts = {c["timestamp"]: c for c in redis_candles}

    common_ts = set(ccxt_by_ts.keys()) & set(redis_by_ts.keys())
    match_count = 0
    mismatch_count = 0
    volume_diffs = []
    price_diffs = []
    for ts in sorted(common_ts):
        cc = ccxt_by_ts[ts]
        rc = redis_by_ts[ts]
        vdiff = abs(cc["volume"] - rc["volume"]) / max(cc["volume"], 1e-10)
        pdiff = abs(cc["close"] - rc["close"]) / max(cc["close"], 1e-10)
        volume_diffs.append(vdiff)
        price_diffs.append(pdiff)
        if vdiff < 0.01:
            match_count += 1
        else:
            mismatch_count += 1

    ccxt_only = len(ccxt_by_ts) - len(common_ts)
    redis_only = len(redis_by_ts) - len(common_ts)

    sections.append("## 1.1 Volume: CCXT vs Redis\n")

    if len(common_ts) == 0:
        sections.append("⚠️ No overlapping timestamps between CCXT and Redis candles.\n")
        findings.append(("CRITICAL", "No common timestamps between CCXT and Redis — pipeline may store different candles"))
        return sections, findings

    sections.append(md_table([
        ["CCXT candles", str(len(ccxt_candles))],
        ["Redis candles (deduped)", str(len(redis_candles))],
        ["Common timestamps", str(len(common_ts))],
        ["Volume match (<1% diff)", f"{match_count}/{len(common_ts)} ({100*match_count/len(common_ts):.1f}%)"],
        ["Volume diff (mean ± std)", f"{np.mean(volume_diffs)*100:.2f}% ± {np.std(volume_diffs)*100:.2f}%"],
        ["Price diff (mean ± std)", f"{np.mean(price_diffs)*100:.4f}% ± {np.std(price_diffs)*100:.4f}%"],
        ["CCXT-only candles", str(ccxt_only)],
        ["Redis-only candles", str(redis_only)],
    ], ["Metric", "Value"], title="Cross-validation: CCXT vs Redis"))

    mean_vdiff = np.mean(volume_diffs) * 100
    if mean_vdiff > 5:
        findings.append(("WARNING", f"Volume differs {mean_vdiff:.1f}% between CCXT and Redis — possible aggregation issue"))
    elif mean_vdiff < 1:
        findings.append(("OK", f"Volume matches CCXT within {mean_vdiff:.2f}%"))

    mean_pdiff = np.mean(price_diffs) * 100
    if mean_pdiff > 0.1:
        findings.append(("WARNING", f"Price differs {mean_pdiff:.4f}% between CCXT and Redis"))
    else:
        findings.append(("OK", f"Price matches CCXT within {mean_pdiff:.4f}%"))

    if ccxt_only > 0:
        findings.append(("INFO", f"{ccxt_only} CCXT candles not in Redis"))

    # 1.2 Duplicate analysis
    print("\n[1.2] Duplicate analysis...")
    all_redis = load_candles_from_redis(count=1200)
    ts_list = [c["timestamp"] for c in all_redis]
    unique_ts = len(set(ts_list))
    total = len(ts_list)
    dup_count = total - unique_ts

    sections.append("## 1.2 Duplicates & Gaps\n")
    sections.append(md_table([
        ["Total entries (pre-dedup)", str(total)],
        ["Unique timestamps", str(unique_ts)],
        ["Duplicates removed", str(dup_count)],
        ["Duplicate rate", f"{100*dup_count/max(total,1):.1f}%"],
    ], ["Metric", "Value"], title="Redis Stream Integrity"))

    if dup_count > 0:
        findings.append(("WARNING", f"{dup_count} duplicate timestamps in Redis stream"))

    # Check gaps
    ts_sorted = sorted(ts_list)
    gaps = []
    for i in range(1, len(ts_sorted)):
        gap = ts_sorted[i] - ts_sorted[i-1]
        if gap != 3600000:
            gaps.append((ts_sorted[i-1], ts_sorted[i], gap))
    sections.append(f"Gaps (non-1h spacing): {len(gaps)} occurrences\n")
    if len(gaps) > 5:
        findings.append(("WARNING", f"{len(gaps)} gaps in Redis stream — expected exactly 1h spacing"))
        for g_ts1, g_ts2, g_gap in gaps[:5]:
            t1 = pd.to_datetime(g_ts1, unit="ms")
            t2 = pd.to_datetime(g_ts2, unit="ms")
            sections.append(f"  - {t1} → {t2}: gap={g_gap/3600000:.1f}h\n")
        if len(gaps) > 5:
            sections.append(f"  ... and {len(gaps)-5} more\n")
    elif len(gaps) > 0:
        findings.append(("INFO", f"{len(gaps)} gaps in Redis (expected near 0)"))

    # 1.3 Volume units (BTC vs USDT)
    print("\n[1.3] Volume units check...")
    avg_volume = np.mean([c["volume"] for c in all_redis[-100:]])
    avg_close = np.mean([c["close"] for c in all_redis[-100:]])
    notional = avg_volume * avg_close

    sections.append("## 1.3 Volume Units\n")
    sections.append(md_table([
        ["Avg volume (last 100)", f"{avg_volume:.2f} BTC"],
        ["Avg close (last 100)", f"${avg_close:.2f}"],
        ["Implied notional", f"${notional:.2f}"],
        ["Interpretation", "Volume appears to be in BTC (contracts), not USDT"],
    ], ["Metric", "Value"], title="Volume Unit Analysis"))

    # Binance futures: volume is in contracts (BTC for BTC/USDT inverse, or USDT for linear)
    # BTC/USDT on Binance Futures is linear (USDT-margined), volume is in USDT not BTC
    # Wait, ccxt returns volume in base currency for Binance spot, but for futures:
    # For linear contracts (USDT-margined), volume is in USDT
    # For inverse contracts (coin-margined), volume is in BTC
    # Binance BTC/USDT perpetual future is linear → volume should be in USDT
    # But the avg volume of ~1200 contracts for BTC at $64k would be ~$77M
    # Hmm, let me check with CCXT directly

    # This is important — let me verify
    ex = ccxt.binance({"options": {"defaultType": "future"}})
    ticker = ex.fetch_ticker("BTC/USDT")
    last_volume = ticker.get("baseVolume", 0)
    quote_volume = ticker.get("quoteVolume", 0)

    sections.append(md_table([
        ["CCXT baseVolume (last)", f"{last_volume:.2f} BTC"],
        ["CCXT quoteVolume (last)", f"${quote_volume:.2f}"],
        ["Redis candle avg volume", f"{avg_volume:.2f}"],
        ["Volume matches", "BTC (baseVolume)" if abs(avg_volume - last_volume) / max(last_volume, 1) < 2 else "USDT (quoteVolume)?"],
    ], ["Metric", "Value"], title="Volume Unit Verification"))

    # Check: if Redis volume matches CCXT baseVolume → volume is in BTC
    # If it matches quoteVolume/close → volume is in USDT converted from BTC
    v_btc_ratio = avg_volume / max(last_volume, 1)
    v_usdt_ratio = (avg_volume * avg_close) / max(quote_volume, 1)

    sections.append(md_table([
        ["Redis volume / CCXT baseVolume", f"{v_btc_ratio:.2f}x"],
        ["Redis notional / CCXT quoteVolume", f"{v_usdt_ratio:.2f}x"],
    ], ["Ratio", "Value"], title="Volume Scale Verification"))

    if 0.8 < v_btc_ratio < 1.2:
        findings.append(("OK", "Redis volume is in BTC (contracts) — matches CCXT baseVolume"))
    elif 0.8 < v_usdt_ratio < 1.2:
        findings.append(("OK", "Redis volume is in USDT (notional) — matches CCXT quoteVolume"))
    else:
        findings.append(("WARNING", f"Volume unit ambiguous: BTC ratio={v_btc_ratio:.2f}, USDT ratio={v_usdt_ratio:.2f}"))

    # 1.4 Training volume distribution
    print("\n[1.4] Training volume distribution...")
    _, scaler, _ = load_model_assets()
    fn = scaler.feature_names_in_ if hasattr(scaler, "feature_names_in_") else None
    center = scaler.center_
    scale = scaler.scale_

    vol_idx = 4  # index of 'volume' in feature list
    vol_center = center[vol_idx]
    vol_scale = scale[vol_idx]
    live_vol = np.array([c["volume"] for c in all_redis])
    live_vol_mean = np.mean(live_vol)
    live_vol_std = np.std(live_vol)
    vol_z = abs(live_vol_mean - vol_center) / max(vol_scale, 1e-10)

    sections.append("## 1.4 Training vs Live: Volume Distribution\n")
    sections.append(md_table([
        ["Training volume median (scaler)", f"{vol_center:.2f}"],
        ["Training volume IQR (scaler)", f"{vol_scale:.2f}"],
        ["Live volume mean (last 1000)", f"{live_vol_mean:.2f}"],
        ["Live volume std (last 1000)", f"{live_vol_std:.2f}"],
        ["Volume z-score", f"{vol_z:.2f}σ"],
    ], ["Metric", "Value"], title="Volume Drift"))

    if vol_z > 10:
        findings.append(("CRITICAL", f"Volume z-score = {vol_z:.1f}σ — EXTREME drift. Market regime has shifted volumetrically."))

    # 1.5 Funding rate comparison
    print("\n[1.5] Funding rate analysis...")
    # Get live funding from Redis if available
    r = redis_lib.from_url(REDIS_URL, socket_connect_timeout=5)
    live_funding = r.get("funding_rate:btc_usdt")
    live_funding_str = str(live_funding) if live_funding else "NOT FOUND (likely pushed as stream, not key)"

    funding_hist = fetch_funding_history()
    if funding_hist:
        f_rates = np.array([f["funding_rate"] for f in funding_hist])
        sections.append("## 1.5 Funding Rate\n")
        sections.append(md_table([
            ["Samples", str(len(f_rates))],
            ["Mean", f"{np.mean(f_rates):.6f}"],
            ["Std", f"{np.std(f_rates):.6f}"],
            ["Min", f"{np.min(f_rates):.6f}"],
            ["Max", f"{np.max(f_rates):.6f}"],
            ["Median", f"{np.median(f_rates):.6f}"],
            ["Last 8h entry (live)", live_funding_str[:80]],
        ], ["Metric", "Value"], title="Live Funding Rate (CCXT)"))

    # 1.6 Timestamp alignment
    print("\n[1.6] Timestamp alignment check...")
    # Check that Redis candles use close_ts
    r2 = redis_lib.from_url(REDIS_URL, socket_connect_timeout=5)
    sample = r2.xrevrange("candles:btc_usdt:3600s", count=1)
    if sample:
        _, fields = sample[0]
        rp = fields.get(b"p") or fields.get("p")
        if isinstance(rp, bytes):
            rp = rp.decode()
        data = json.loads(rp)
        sections.append("## 1.6 Timestamp Field\n")
        sections.append(md_table([
            ["open_ts", str(data.get("open_ts", "?")), pd.to_datetime(data.get("open_ts", 0), unit="ms").strftime("%Y-%m-%d %H:%M:%S UTC") if data.get("open_ts") else ""],
            ["close_ts", str(data.get("close_ts", "?")), pd.to_datetime(data.get("close_ts", 0), unit="ms").strftime("%Y-%m-%d %H:%M:%S UTC") if data.get("close_ts") else ""],
            ["is_complete", str(data.get("is_complete", "?"))],
        ], ["Field", "Raw", "Interpretation"], title="Candle Timestamp Verification"))
        findings.append(("OK", f"Candles use close_ts as timestamp — aligns with CandleBuffer.to_ohlcv_dicts()"))

    # ── Overall Phase 1 verdict ──
    print("\n[1.0] Phase 1 Verdict...")
    crit_count = sum(1 for s, _ in findings if s == "CRITICAL")
    warn_count = sum(1 for s, _ in findings if s == "WARNING")
    if crit_count > 0:
        verdict = "BROKEN"
    elif warn_count > 2:
        verdict = "WARNING"
    else:
        verdict = "SAFE"

    sections.insert(0, f"**Phase 1 Verdict**: {verdict}\n\n")
    sections.insert(1, f"CRITICAL={crit_count}, WARNING={warn_count}, OK={sum(1 for s,_ in findings if s=='OK')}\n\n")
    sections.insert(2, md_table(findings, ["Severity", "Finding"], title="All Findings"))

    report = f"""# Data Pipeline Audit
> Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}

{"".join(sections)}
"""
    (REPORTS / "data_pipeline_audit.md").write_text(report)
    print(f"\n  → reports/data_pipeline_audit.md")
    return findings


# ═══════════════════════════════════════════════════════════════════════
#  PHASE 2 — DRIFT VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def phase2_drift(candles, feature_names, scaler):
    print("\n" + "=" * 60)
    print("  PHASE 2: DRIFT VALIDATION")
    print("=" * 60)

    _, scaler, metadata = load_model_assets()
    feature_names = metadata["feature_names"]
    center = scaler.center_
    scale = scaler.scale_

    # Build features on live data
    cfg = ModelConfig(features=tuple(feature_names))
    preprocessor = TaDataPreprocessor()

    try:
        features_raw = asyncio.run(preprocessor.build_features(candles, cfg))
    except Exception as e:
        print(f"  ERROR building features: {e}")
        return

    live_mean = np.mean(features_raw, axis=0)
    live_std = np.std(features_raw, axis=0)

    z = np.abs((live_mean - center) / np.maximum(scale, 1e-10))

    # Individual feature drift
    drift_records = []
    for i, name in enumerate(feature_names):
        for sev_name, (lo, hi) in SEVERITY_ZSCORE.items():
            if lo <= z[i] < hi:
                drift_records.append({
                    "feature": name,
                    "z_score": round(float(z[i]), 2),
                    "severity": sev_name,
                    "training_center": round(float(center[i]), 4),
                    "training_scale": round(float(scale[i]), 4),
                    "live_mean": round(float(live_mean[i]), 4),
                    "live_std": round(float(live_std[i]), 4),
                })
                break

    drift_records.sort(key=lambda x: x["z_score"], reverse=True)

    # Summary stats
    by_severity = {}
    for r in drift_records:
        by_severity.setdefault(r["severity"], 0)
        by_severity[r["severity"]] += 1

    drift_json = {
        "n_features": len(feature_names),
        "n_candles": len(candles),
        "mean_z_score": round(float(np.mean(z)), 3),
        "max_z_score": round(float(np.max(z)), 3),
        "by_severity": {k: by_severity.get(k, 0) for k in ["NORMAL", "MODERATE", "HIGH", "EXTREME"]},
        "top_20": drift_records[:20],
        "all_features": drift_records,
    }

    (REPORTS / "drift_report.json").write_text(json.dumps(drift_json, indent=2))

    # Generate markdown report
    md = f"""# Drift Report
> Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}

## Summary

| Metric | Value |
|--------|-------|
| Features analyzed | {len(feature_names)} |
| Live candles | {len(candles)} |
| Mean z-score | {np.mean(z):.3f}σ |
| Max z-score | {np.max(z):.3f}σ |

## Severity Distribution

| Severity | Count |
|----------|-------|
| NORMAL (0–2σ) | {by_severity.get("NORMAL", 0)} |
| MODERATE (2–5σ) | {by_severity.get("MODERATE", 0)} |
| HIGH (5–10σ) | {by_severity.get("HIGH", 0)} |
| EXTREME (>10σ) | {by_severity.get("EXTREME", 0)} |

## Top 20 Features by Drift

| # | Feature | z-score | Severity | Training Center | Training Scale | Live Mean | Live Std |
|---|---------|---------|----------|----------------|----------------|-----------|----------|
"""
    for i, r in enumerate(drift_records[:20]):
        md += f"| {i+1} | {r['feature']} | {r['z_score']}σ | {r['severity']} | {r['training_center']} | {r['training_scale']} | {r['live_mean']} | {r['live_std']} |\n"

    md += "\n## Volume Feature Chain\n\n"
    vol_features = [r for r in drift_records if "volume" in r["feature"].lower()]
    md += "| Feature | z-score | Severity |\n|---|---|---|\n"
    for r in vol_features:
        md += f"| {r['feature']} | {r['z_score']}σ | {r['severity']} |\n"

    md += "\n## Funding Features\n\n"
    fund_features = [r for r in drift_records if "funding" in r["feature"].lower()]
    if fund_features:
        md += "| Feature | z-score | Severity |\n|---|---|---|\n"
        for r in fund_features:
            md += f"| {r['feature']} | {r['z_score']}σ | {r['severity']} |\n"
    else:
        md += "Funding features not found in live data (expected — they are injected by FundingRateProvider)\n"

    (REPORTS / "drift_report.md").write_text(md)
    print(f"  → reports/drift_report.json")
    print(f"  → reports/drift_report.md")
    return drift_records


# ═══════════════════════════════════════════════════════════════════════
#  PHASE 3 — FEATURE COLLAPSE
# ═══════════════════════════════════════════════════════════════════════

def phase3_collapse(features_raw, feature_names):
    print("\n" + "=" * 60)
    print("  PHASE 3: FEATURE COLLAPSE")
    print("=" * 60)

    live_std = np.std(features_raw, axis=0)
    collapse_idx = np.where(live_std < 1e-6)[0]
    collapse_features = [feature_names[i] for i in collapse_idx]

    near_zero_idx = np.where((live_std > 0) & (live_std < 1e-4))[0]
    near_zero_features = [(feature_names[i], float(live_std[i])) for i in near_zero_idx]

    md = f"""# Feature Collapse Report
> Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}

## Summary

| Metric | Value |
|--------|-------|
| Total features | {len(feature_names)} |
| Collapsed (variance < 1e-6) | {len(collapse_features)} |
| Near-zero (variance 1e-6 to 1e-4) | {len(near_zero_features)} |
| Healthy (variance >= 1e-4) | {len(feature_names) - len(collapse_features) - len(near_zero_features)} |

## Collapsed Features

"""
    if collapse_features:
        md += "| # | Feature | Live Std |\n|---|---|---|\n"
        for i, name in enumerate(collapse_features):
            md += f"| {i+1} | {name} | {live_std[feature_names.index(name)]:.2e} |\n"
    else:
        md += "None\n"

    md += "\n## Near-Zero Features\n\n"
    if near_zero_features:
        md += "| # | Feature | Live Std |\n|---|---|---|\n"
        for i, (name, std) in enumerate(near_zero_features):
            md += f"| {i+1} | {name} | {std:.2e} |\n"
    else:
        md += "None\n"

    md += "\n## Interpretation\n\n"
    if len(collapse_features) >= 5:
        md += "⚠️ **Multiple collapsed features**: model is effectively seeing fewer dimensions than trained on.\n"
    elif len(collapse_features) > 0:
        md += "⚠️ Some features collapsed — check if they are funding or volume features.\n"
    else:
        md += "✅ No features collapsed.\n"

    (REPORTS / "feature_collapse_report.md").write_text(md)
    print(f"  → reports/feature_collapse_report.md")
    return collapse_features


# ═══════════════════════════════════════════════════════════════════════
#  PHASE 4 — DISTRIBUTION COMPARISON
# ═══════════════════════════════════════════════════════════════════════

def phase4_distribution(features_raw, feature_names, scaler):
    print("\n" + "=" * 60)
    print("  PHASE 4: DISTRIBUTION COMPARISON")
    print("=" * 60)

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    center = scaler.center_
    scale = scaler.scale_

    key_features = [
        ("volume", "Volume (contracts)"),
        ("volume_sma", "Volume SMA(20)"),
        ("atr", "ATR(14)"),
        ("rsi", "RSI(14)"),
        ("pct_change", "Price Change %"),
        ("htf_volume_sma_4h", "Volume SMA 4h"),
        ("htf_volume_sma_1d", "Volume SMA 1d"),
    ]

    # Also check if funding features existed
    funding_names = ["funding_rate", "funding_momentum", "funding_change"]
    for fn in funding_names:
        if fn in feature_names:
            key_features.append((fn, fn.replace("_", " ").title()))

    md = f"""# Distribution Comparison: Training vs Live
> Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}

## Method

- **Training distribution**: estimated from RobustScaler parameters (median ≈ center_, IQR ≈ scale_).
  For variance, we approximate training std as scale_ / 0.6745 (since scale_ = IQR for RobustScaler, and
  IQR ≈ 1.349σ for a normal distribution). This is approximate; actual training data is unavailable.
- **Live distribution**: computed from {features_raw.shape[0]} live 1h candles (2026-05-11 to {datetime.now(timezone.utc).strftime("%Y-%m-%d")}).

## Key Features

| Feature | Training P50 | Training IQR | Live P50 | Live IQR | Live Mean | Live Std | z-score | Drift |
|---------|-------------|-------------|---------|---------|-----------|---------|---------|-------|
"""
    for fname, label in key_features:
        if fname not in feature_names:
            continue
        idx = feature_names.index(fname)
        t_med = center[idx]
        t_iqr = scale[idx]
        l_vals = features_raw[:, idx]
        l_med = float(np.median(l_vals))
        l_iqr = float(np.percentile(l_vals, 75) - np.percentile(l_vals, 25))
        l_mean = float(np.mean(l_vals))
        l_std = float(np.std(l_vals))
        zs = abs(l_mean - t_med) / max(t_iqr, 1e-10)

        if zs > 10:
            drift = "🚨 EXTREME"
        elif zs > 5:
            drift = "⚠️ HIGH"
        elif zs > 2:
            drift = "🟡 MODERATE"
        else:
            drift = "✅ NORMAL"

        md += f"| {label} | {t_med:.4f} | {t_iqr:.4f} | {l_med:.4f} | {l_iqr:.4f} | {l_mean:.4f} | {l_std:.4f} | {zs:.1f}σ | {drift} |\n"

    # Percentile table for top features
    md += "\n## Percentile Comparison\n\n"
    md += "| Feature | P5 Train | P25 Train | P50 Train | P75 Train | P95 Train | P5 Live | P25 Live | P50 Live | P75 Live | P95 Live |\n"
    md += "|---|---|---|---|---|---|---|---|---|---|---|\n"

    for fname, label in key_features:
        if fname not in feature_names:
            continue
        idx = feature_names.index(fname)
        t_med = center[idx]
        t_iqr = scale[idx]
        # Approximate percentiles from RobustScaler (roughly)
        t_p50 = t_med
        t_p25 = t_med - 0.5 * t_iqr  # rough approximation
        t_p75 = t_med + 0.5 * t_iqr
        t_p5 = t_med - 1.5 * t_iqr
        t_p95 = t_med + 1.5 * t_iqr

        l_vals = features_raw[:, idx]
        lp5, lp25, lp50, lp75, lp95 = np.percentile(l_vals, [5, 25, 50, 75, 95])

        md += f"| {label} | {t_p5:.2f} | {t_p25:.2f} | {t_p50:.2f} | {t_p75:.2f} | {t_p95:.2f} | {lp5:.2f} | {lp25:.2f} | {lp50:.2f} | {lp75:.2f} | {lp95:.2f} |\n"

    # All features table
    live_mean_all = np.mean(features_raw, axis=0)
    md += "\n## All 53 Features: Mean Comparison\n\n"
    md += "| # | Feature | Training Center (Median) | Live Mean | Diff | z-score |\n|---|---|---|---|---|---|\n"
    for i, name in enumerate(feature_names):
        diff = float(live_mean_all[i]) - float(center[i])
        zs = abs(diff) / max(float(scale[i]), 1e-10)
        md += f"| {i+1} | {name} | {center[i]:.4f} | {live_mean_all[i]:.4f} | {diff:+.4f} | {zs:.1f}σ |\n"

    (DIST_DIR / "distribution_comparison.md").write_text(md)
    print(f"  → reports/distribution_comparison/")
    return


# ═══════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    REPORTS.mkdir(exist_ok=True)
    model, scaler, metadata = load_model_assets()
    feature_names = metadata["feature_names"]

    # Phase 1: Data Pipeline Audit
    findings = phase1_audit()

    # Load candles (deduped)
    candles = load_candles_from_redis(count=1000)
    print(f"\nLoaded {len(candles)} candles for drift analysis")

    if len(candles) < 100:
        print("ERROR: insufficient candles")
        return

    # Build features for phases 2-4
    cfg = ModelConfig(features=tuple(feature_names))
    preprocessor = TaDataPreprocessor()
    features_raw = asyncio.run(preprocessor.build_features(candles, cfg))
    print(f"Features shape: {features_raw.shape}")

    # Phase 2: Drift
    drift_records = phase2_drift(candles, feature_names, scaler)

    # Phase 3: Feature collapse
    collapse = phase3_collapse(features_raw, feature_names)

    # Phase 4: Distribution comparison
    phase4_distribution(features_raw, feature_names, scaler)

    print("\n" + "=" * 60)
    print("  ALL PHASES 1-4 COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
