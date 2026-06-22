# Argos ATS — Plan de Negocio

> Documento vivo. Ideas, posibilidades y decisiones sobre el modelo de negocio.
> Última actualización: 2026-06-13

---

## 1. Visión / Propuesta de valor

<!-- ¿Qué problema resuelve el ATS? ¿Para quién? ¿Qué lo diferencia de otras herramientas? -->

Preguntas guía:
- ¿Automatizar trading para personas sin tiempo técnico?
- ¿Señales más inteligentes que las de redes sociales?
- ¿Backtesting confiable antes de arriesgar capital?

---

## 2. Modelo de monetización (ideas)

### Posibilidades
- **Suscripción mensual/anual** (plan Free / Basic / Pro)
- **Freemium**: señales gratis con límite, alerts pagos
- **Comisión por profit share** (% de ganancias)
- **Pago por señal** (microtransacciones)
- **Licencia enterprise** para fondos o grupos de trading
- **White label**: otros brands usan nuestra infraestructura

### Medios de pago
- Stripe (tarjeta)
- Crypto (USDT, BTC)
- PayPal

---

## 3. Perfiles de usuario / buyer personas

| Perfil | Descripción | Dolor | Dispuesto a pagar |
|--------|-------------|-------|-------------------|
| Trader retail | Opera manual, busca señales | Falta de tiempo/ disciplina | $ / mes |
| Trader semi-automatizado | Usa bots simples | Quiere estrategias más sofisticadas | $$ / mes |
| Pequeño fondo | Gestiona capital de terceros | Necesita reporting y risk management | $$$ / mes |
| Crypto-curioso | principiante | No sabe por dónde empezar | $ (freemium) |

---

## 4. Features por segmento

| Feature | Free | Basic | Pro | Notas |
|---------|------|-------|-----|-------|
| Alertas Telegram | ✅ | ✅ | ✅ | Ya implementado |
| Alertas Discord | ✅ | ✅ | ✅ | Ya implementado |
| Estrategias disponibles | 1 | 3 | Ilimitadas | |
| Backtesting | ❌ | ✅ | ✅ | |
| Símbolos permitidos | 1 par | 5 pares | Ilimitados | |
| Web dashboard | ❌ | ❌ | ✅ | Por construir |
| API propia | ❌ | ❌ | ✅ | |
| Risk personalizado | ❌ | ❌ | ✅ | ATR, drawdown, etc. |
| Reporting semanal | ❌ | ✅ | ✅ | |

---

## 5. Canales de distribución

- **Telegram** ✅ listo — canal de alerts, futuramente bot interactivo
- **Discord** ✅ listo — webhooks para comunidades
- **Web dashboard** ❌ por construir — React/Vite
- **App mobile** ❌ por decidir — React Native
- **API pública** ❌ por definir — para integraciones externas

---

## 6. Competencia / alternativas

| Producto | Tipo | Diferenciador de Argos |
|----------|------|------------------------|
| 3Commas | Bot + SmartTrade | Más caro, menos transparencia en risk |
| Cryptohopper | Bot cloud | Estrategias en la nube, código cerrado |
| TradingView alerts | Señales vía webhook | Sin backtesting nativo |
| HaasOnline | Bot avanzado | Caro, curva de aprendizaje alta |
| Coinrule | Bot reglas simples | Muy básico |

**Diferenciadores potenciales de Argos:**
- Risk management implacable (invariantes duras)
- Cero pérdida >1% por trade
- Código abierto / transparente
- Microservicios desacoplados (fácil de extender)

---

## 7. Riesgos y regulación

- **API de exchanges**: Binance, Bybit, etc. pueden restringir acceso
- **Compliance**: en algunos países operar bots de futuros puede requerir registro
- **Responsabilidad**: P&L terms of service para usuarios
- **Latencia / slippage**: en LIVE mode, ejecución real puede diferir del backtest
- **Seguridad**:API keys de usuarios requieren encriptación robusta

---

## 8. Notas / ideas sueltas

> Acá tiramos ideas sin filtrar. Después se mueven a donde corresponda.

- [ ] ¿Dar acceso vía Telegram OAuth para login sin password?
- [ ] ¿Plan de afiliados (referral) para crecer orgánicamente?
- [ ] ¿Modo "copy-trading" donde usuarios siguen a un trader?
- [ ] ¿Soporte para múltiples exchanges (Bybit, OKX, Kraken)?
- [ ] ¿Bot en español (latam) como nicho desatendido?
- [ ] ¿Prueba gratuita de 7 días sin tarjeta?
- [ ] ¿Community/DAO governance para decisiones de desarrollo?

---

## 9. Próximos pasos

- [ ] Definir modelo de pricing
- [ ] Elegir stack de pagos (Stripe vs crypto)
- [ ] Construir MVP de web dashboard
- [ ] Implementar manejo de usuarios (whitelist primero)
- [ ] Validar con al menos 3 potenciales clientes
