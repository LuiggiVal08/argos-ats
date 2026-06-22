# Drift Report
> Generated 2026-06-21 17:40:46 UTC

## Summary

| Metric | Value |
|--------|-------|
| Features analyzed | 53 |
| Live candles | 999 |
| Mean z-score | 3.489σ |
| Max z-score | 50.445σ |

## Severity Distribution

| Severity | Count |
|----------|-------|
| NORMAL (0–2σ) | 45 |
| MODERATE (2–5σ) | 3 |
| HIGH (5–10σ) | 1 |
| EXTREME (>10σ) | 4 |

## Top 20 Features by Drift

| # | Feature | z-score | Severity | Training Center | Training Scale | Live Mean | Live Std |
|---|---------|---------|----------|----------------|----------------|-----------|----------|
| 1 | htf_volume_sma_1d | 50.44σ | EXTREME | 34052.217 | 27704.3082 | 1431585.0799 | 160471.9736 |
| 2 | htf_volume_sma_4h | 42.81σ | EXTREME | 5263.3258 | 5653.3798 | 247265.9551 | 56413.8855 |
| 3 | volume_sma | 35.53σ | EXTREME | 1273.6865 | 1683.0314 | 61078.7586 | 21612.19 |
| 4 | volume | 25.45σ | EXTREME | 1205.3041 | 2330.1699 | 60519.7758 | 30733.9955 |
| 5 | htf_obv_4h | 5.01σ | HIGH | 1255031.1227 | 1361043.9765 | -5569695.902 | 2887701.3314 |
| 6 | htf_obv_1d | 4.87σ | MODERATE | -4179150.7856 | 669794.9734 | -7438590.4979 | 4627684.8317 |
| 7 | obv | 4.24σ | MODERATE | 477490.299 | 578518.7325 | -1977618.3538 | 1385256.4742 |
| 8 | htf_macd_signal_1d | 2.2σ | MODERATE | 20.0082 | 1848.9862 | -4042.8435 | 338.21 |
| 9 | htf_macd_1d | 1.95σ | NORMAL | 19.2061 | 1950.2337 | -3782.1816 | 548.3072 |
| 10 | htf_rsi_1d | 1.12σ | NORMAL | 50.116 | 18.5249 | 29.4236 | 8.3877 |
| 11 | htf_ema_slow_1d | 1.02σ | NORMAL | 60755.8666 | 59293.4778 | 0.0 | 0.0 |
| 12 | htf_macd_signal_4h | 0.83σ | NORMAL | -1.2649 | 609.236 | -505.5117 | 693.084 |
| 13 | funding_rate | 0.75σ | NORMAL | 0.0061 | 0.0081 | 0.0 | 0.0 |
| 14 | htf_macd_4h | 0.72σ | NORMAL | -4.1617 | 648.6871 | -470.3551 | 745.0997 |
| 15 | htf_macd_hist_1d | 0.65σ | NORMAL | 6.6599 | 598.4374 | 395.5865 | 140.2939 |
| 16 | macd_signal | 0.48σ | NORMAL | 3.4923 | 264.7204 | -123.8889 | 317.1631 |
| 17 | macd | 0.43σ | NORMAL | 1.123 | 280.6801 | -120.4897 | 332.3882 |
| 18 | htf_rsi_4h | 0.43σ | NORMAL | 49.7785 | 17.644 | 42.1041 | 13.0623 |
| 19 | htf_adx_4h | 0.37σ | NORMAL | 25.7231 | 16.3059 | 31.682 | 20.9201 |
| 20 | htf_atr_1d | 0.33σ | NORMAL | 2165.0783 | 1883.8663 | 1534.5506 | 1066.6915 |

## Volume Feature Chain

| Feature | z-score | Severity |
|---|---|---|
| htf_volume_sma_1d | 50.44σ | EXTREME |
| htf_volume_sma_4h | 42.81σ | EXTREME |
| volume_sma | 35.53σ | EXTREME |
| volume | 25.45σ | EXTREME |

## Funding Features

| Feature | z-score | Severity |
|---|---|---|
| funding_rate | 0.75σ | NORMAL |
| funding_momentum | 0.0σ | NORMAL |
| funding_change | 0.0σ | NORMAL |
