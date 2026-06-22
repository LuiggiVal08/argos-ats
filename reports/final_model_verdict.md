# Final Model Verdict — argos-ats v1.0.0
> Generated 2026-06-21
>
> Answers 8 structural questions about the model's failure and path forward.

---

## 1. ¿El drift es real o hay bug?

**Respuesta: HAY BUG.**

El drift observado (25–50σ en volumen) NO es un cambio de régimen genuino.

**Evidencia**:
- El `CandleBuilder` acumula volume desde trades individuales de Binance Spot
  (vía WebSocket `btcusdt@trade`). El volume promedio por vela es ~55,000 (en 
  alguna unidad).
- CCXT `fetch_ohlcv` para el mismo par en spot retorna ~273 BTC/hr.
- La diferencia es ~200x — imposible como cambio de régimen.
- Causa probable: duplicación de ticks (sin dedup por tradeId en el loop de 
  consumo del analytics-engine) o una diferencia en cómo se acumula volume
  entre la API REST (OHLCV agregado) y el stream de trades individuales.

**Además**: 164/200 velas recientes en Redis son "tick-built" (range ancho, 
volume inflado). Solo 11/200 son "spot-like" (matching CCXT spot). Esto 
confirma que el pipeline produce velas de calidad inconsistente.

**Conclusión**: El drift de volumen es ARTIFICIAL, no real.

---

## 2. ¿El modelo murió por cambio de régimen?

**Respuesta: NO principalmente. Murió por un bug en el pipeline.**

El modelo fue entrenado con volumen en BTC (CCXT spot, median ~1,205 BTC/hr). 
En live, recibe volumen inflado (~55,000 "BTC"/hr). El RobustScaler transforma 
esto a valores extremos, y las features de volumen (que son 4/53) dominan el 
logit del modelo, colapsándolo a una clase.

Había un cambio de régimen real (el volumen de 2026 es distinto a 2022), pero 
la magnitud del drift observado (50σ) es 10x mayor que cualquier cambio real 
posible.

---

## 3. ¿El volumen absoluto debe eliminarse?

**Respuesta: SÍ.**

`volume`, `volume_sma`, `htf_volume_sma_4h`, `htf_volume_sma_1d`, `obv` y 
sus variantes MTF (total: 7 features) dependen de volume absoluto y son 
no-estacionarias. Deben reemplazarse por métricas relativas:

- `relative_volume = volume / sma_50(volume)`
- `volume_zscore_50 = (volume - mean_50) / std_50`

Mientras el pipeline no garantice que el volume esté en las mismas unidades 
que el training, cualquier feature de volume absoluto es un riesgo.

---

## 4. ¿El funding aporta señal?

**Respuesta: NO SE PUEDE DETERMINAR.**

Las features de funding (`funding_rate`, `funding_momentum`, `funding_change`)
se inyectan en vivo vía `FundingRateProvider` solo en la última fila 
(`features_raw[-1, i] = extra[name]`). En el test KILL/KEEP, el funding se 
setea a 0.0 (porque el script no usó el provider). En producción, solo la 
última fila tiene funding real, el resto es 0.0.

Para evaluar si funding aporta señal, habría que:
1. Inyectar funding histórico a todas las filas (no solo la última)
2. Re-ejecutar el test KILL/KEEP

**Estimación**: El funding en BTC/USDT perpetual es típicamente pequeño 
(mean ~0.001%, max ~0.01%). Con coeffs pequeños en el modelo, probablemente
aporta señal marginal. No es prioritario.

---

## 5. ¿Tiene sentido reentrenar?

**Respuesta: SÍ, pero solo después de arreglar el pipeline.**

Reentrenar con el pipeline roto reproduciría el mismo KILL. El orden es:

1. **Fix volume pipeline** (1-2 días)
2. **Feature redesign** (2-3 días) — reemplazar volume absoluto por relativo
3. **Retrain with V2 features** (1 día)
4. **Validate with KILL/KEEP test** (0.5 día)

Solo si el step 4 da KEEP tiene sentido desplegar.

---

## 6. ¿Conviene un único modelo o varios modelos?

**Respuesta: Único modelo por ahora.**

La complejidad de múltiples modelos (entrenamiento, deploy, monitoreo,
transiciones entre regímenes) no se justifica hasta que el modelo único 
demuestre edge estable. Recomendación:

- **Fase 1**: Modelo único con features robustas (V2)
- **Fase 2**: Si el modelo único da MARGINAL, agregar regime gating
- **Fase 3**: Si hay suficiente data por régimen, considerar modelos
  especializados

---

## 7. ¿ARGOS V2 debería ser regime-aware?

**Respuesta: SÍ, pero no es prioritario.**

Un regime detector (Propuesta en `reports/regime_detector_design.md`) 
mejoraría la robustez, pero tiene sentido solo después de que el modelo base
funcione. La prioridad es:

1. Pipeline fix + feature redesign + retrain
2. KILL/KEEP pasa a KEEP
3. Recién entonces: regime detector

---

## 8. Prioridad recomendada

| Prioridad | Acción | Dependencia | Esfuerzo |
|-----------|--------|-------------|----------|
| **P0** | Fix volume pipeline (CandleBuilder vs CCXT mismatch) | — | 1-2 días |
| **P1** | Feature redesign (relative volume, ATR norm, etc.) | P0 | 2-3 días |
| **P2** | Retrain v2 model | P0 + P1 | 1 día |
| **P3** | KILL/KEEP validation on v2 | P2 | 0.5 día |
| **P4** | Regime detector | P3 = KEEP | 3-5 días |
| **P5** | Rolling window retraining | P4 | 2-3 días |
| **P6** | Regime-specialized models | P5 | 5-7 días |

---

## Summary Table

| Problema | Evidencia | Severidad | Acción recomendada |
|----------|-----------|-----------|-------------------|
| Volume pipeline bug | 200x mismatch Redis vs CCXT | 🔴 CRITICAL | Fix CandleBuilder or consumer dedup |
| Absolute volume features | 4 features with 25-50σ drift | 🔴 CRITICAL | Replace with relative volume (V2) |
| Pipeline mixed candle quality | 164/200 tick-built, 11/200 spot-like | 🟡 HIGH | Investigate CandleBuilder lifecycle |
| 3-class code assumption | Model binary, code expects 3-class | 🟡 HIGH | Fix streaming_inference.py policy layer |
| No trade ID dedup | Duplicate ticks inflate volume | 🟡 HIGH | Add trade_id tracking in consumer |
| Funding not evaluated | Not injected in test | 🟢 MEDIUM | Evaluate after pipeline fix |
| No regime detection | Model works or fails silently | 🟢 LOW | Implement after v2 model validated |
