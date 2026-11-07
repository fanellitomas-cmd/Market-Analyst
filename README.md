# Market Analyst

Sitio personal de análisis de mercado: indicadores macroeconómicos por país y análisis de
activos contra el S&P 500 (beta, correlación, volatilidad, caídas). Todo con fuentes
**gratuitas y sin clave de API**.

| Sección | Qué muestra | Fuente |
|---|---|---|
| **Panorama** | EE.UU. de un vistazo (Fed, 10 años, pendiente, inflación, desempleo, VIX) y último dato anual de PBI, crecimiento, PBI per cápita, inflación, desempleo, cuenta corriente y deuda, por país | Banco Mundial |
| **EE.UU.** | Calendario económico de la semana (horario de Buenos Aires) y sorpresas de CPI, PPI, PCE, NFP, desempleo y ventas minoristas frente al pronóstico. Fed, bonos del Tesoro (3M a 30A), pendientes 10A−2A y 10A−3M, hipotecas, inflación (IPC, núcleo, PCE núcleo, esperada), desempleo, empleos creados, pedidos de desempleo, PBI, VIX, spread high yield, dólar y petróleo. Historia de cada serie y curva de rendimientos hoy / hace 1 mes / hace 1 año | FRED (Reserva Federal de St. Louis) |
| **Liquidez** | Balances de la Fed, el BCE y el BoJ; liquidez neta de la Fed (activos − TGA − RRP); DXY e índice amplio del dólar; spreads high yield y grado de inversión; HYG/LQD; condiciones financieras (NFCI); tasa real a 10 años; cíclicos/defensivos. Cambio a 1 mes, 3 meses y 1 año | FRED + Yahoo Finance |
| **Argentina** | Dólar oficial, mayorista, MEP, CCL, blue, cripto y tarjeta con su brecha; evolución de oficial/MEP/CCL/blue; riesgo país; reservas, base monetaria, tasa de política, BADLAR, TAMAR, plazo fijo, inflación mensual/interanual/esperada, CER y UVA | BCRA (oficial), DolarApi y ArgentinaDatos (comunitarias) |
| **Técnico** | Régimen del activo antes de cualquier oscilador (tendencia con EMA 20/50/200 y ADX > 25, o rango, con o sin compresión de Bollinger); AVWAP anclados en el último balance (8-K), máximo y mínimo de 52 semanas, inicio del año y una fecha a elección; perfil de volumen de 60 días (POC, VAH, VAL); volumen relativo y su desvío (σ). Gráfico de velas con TradingView Lightweight Charts | Yahoo Finance + SEC |
| **Empresas** | Pestaña **Forense y valuación**: Beneish M-Score (8 variables) con sus componentes, Altman Z (original para industriales o Z'' para el resto, según el código SIC), quick ratio, ROIC frente a WACC (prima de riesgo editable), FCF menos compensación en acciones y percentil del P/E de 5 años sin mirar datos del futuro. Pestaña **Resultados**: últimos 12 trimestres: ingresos, variación anual, márgenes bruto/operativo/neto, ganancia neta y por acción; reacción de la acción (y exceso sobre el SPY) a 1, 5 y 20 ruedas de cada publicación de resultados | SEC EDGAR + Yahoo Finance |
| **Screener** | GARP sobre un universo editable (hasta 150 empresas): P/E < 20, ingresos +20 % anual en 5 años, margen operativo en expansión y ROIC > WACC, con cumple / no cumple / sin dato por criterio | SEC + Yahoo + FRED |
| **Sistema** | Estado del agente 24/7: si está activo, sus tareas programadas (hora de Buenos Aires), la última corrida de cada una, los avisos enviados por WhatsApp y botones para probar | — |
| **Macro global** | Series históricas por indicador, comparando hasta 8 países (EE.UU., Canadá, Argentina, Brasil, México, Chile, Zona Euro, Alemania, Francia, Italia, Reino Unido, Japón, China) | Banco Mundial |
| **Mercados** | Beta, correlación, R², rendimiento, volatilidad anualizada y caída máxima contra un benchmark (SPY, QQQ, IWM, EEM, ACWI); correlación y beta móviles; comparación de hasta 15 activos | Yahoo Finance |

## Principio: cero dato inventado

Heredado del proyecto Financiero. Si la fuente no trae un dato, se muestra "—" con su motivo;
nunca un `0` ni un valor interpolado. Cada número muestra la fuente, la hora de la consulta y,
en los datos macro, el año al que corresponde. Con menos de 60 ruedas en común, beta y
correlación quedan como "no disponible" en lugar de mostrar una cifra poco confiable.

## Agente 24/7

Un proceso aparte (`python -m app.worker`, o el servicio `worker` de Docker) corre tareas
programadas en hora de Buenos Aires, conoce los feriados de la bolsa de Nueva York y avisa por
WhatsApp (CallMeBot, gratis). Cada corrida y cada aviso quedan registrados en SQLite y se ven en
**Sistema**. Principio "sin ruido": una tarea sólo avisa cuando hay algo que decir.

Tareas actuales:
- **Chequeo de fuentes** (días hábiles de NY, 07:15): verifica que cada fuente responda y avisa
  sólo si alguna falla.
- **Calendario económico** (todos los días, 06:30): guarda los datos de la semana con su
  pronóstico (ForexFactory).
- **Datos publicados y sorpresas** (días hábiles 10:05, 11:05, 12:05 y 17:05): toma el dato real
  de FRED apenas sale y avisa sólo si se desvía del pronóstico más que el umbral del indicador
  (por ejemplo, 0,1 p.p. en el IPC o 50 mil en el NFP). Cada sorpresa se avisa una sola vez.
- **Screener GARP** (días hábiles 21:00): analiza el universo con el motor forense y avisa sólo
  cuando una empresa entra al filtro.
- **Latido semanal** (lunes 08:00): confirma que el agente sigue vivo, con un resumen de la semana.

Para instalarlo en tu PC o en un servidor gratuito, ver **[DEPLOY.md](DEPLOY.md)**.

## Estructura

```
backend/    API en Python (FastAPI) y agente (APScheduler)
  app/agent/      tareas programadas del agente
  app/notify/     canales de aviso (WhatsApp vía CallMeBot)
  app/container.py arma clientes y servicios (compartido por API y agente)
  app/worker.py   proceso del agente 24/7
  app/sources/    clientes de Banco Mundial, FRED, BCRA, DolarApi, ArgentinaDatos,
                  SEC EDGAR y Yahoo Finance
  app/services/   macro.py (países), us_macro.py (FRED), ar_macro.py (Argentina),
                  earnings.py (SEC), technical.py (régimen, AVWAP, perfil), liquidity.py,
                  econ_calendar.py, quant.py (fórmulas), markets.py
  app/api/        endpoints /api/auth, /api/macro, /api/us, /api/ar, /api/markets,
                  /api/earnings, /api/system,
                  /api/liquidity, /api/calendar, /api/technical
  tests/          tests con respuestas simuladas (no salen a la red)
frontend/   sitio en Next.js (App Router) + Recharts
```

El navegador sólo habla con Next.js; `next.config.ts` reenvía `/api/*` al backend. Así la
cookie de sesión (httpOnly) queda en el mismo dominio y no hace falta CORS.

## Cómo correrlo en tu máquina

La forma más simple es Docker: ver **[DEPLOY.md](DEPLOY.md)**. Para desarrollar sin Docker:

Requisitos: Python 3.11+ y Node 20+.

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # en Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
python scripts/hash_password.py  # elegí tu contraseña y pegá las 2 líneas que imprime en .env
# en .env completá también SEC_USER_AGENT="Tu Nombre tu@email.com" (lo exige la SEC)
uvicorn app.main:app --reload --port 8000
# en otra terminal, el agente 24/7:
python -m app.worker
```

### 2. Frontend (en otra terminal)

```bash
cd frontend
npm install
npm run dev
```

Abrí http://localhost:3000 e ingresá con el usuario de `APP_USERNAME` (por defecto `admin`)
y tu contraseña.

## Checks

```bash
cd backend && pytest && ruff check . && mypy app tests
cd frontend && npm run typecheck && npm run build
```

## Notas sobre las fuentes

- **Banco Mundial**: datos anuales, publicados con 1–2 años de rezago. Se cachean 6 horas.
- **FRED**: se usa la descarga CSV pública (`fredgraph.csv`), que no pide clave. Cada serie se
  baja una vez desde 1999 y se cachea 3 horas. La inflación interanual y los empleos creados se
  calculan sobre el índice y el total publicados; si falta el mes base, ese mes se omite.
- **BCRA** (API de Principales Variables v4.0): las variables se buscan por su descripción y no
  por número, porque el BCRA ya las renumeró entre versiones. La interfaz muestra la descripción
  exacta y el id de la serie que encontró. Si el BCRA deja de publicar una, aparece "sin dato".
- **DolarApi / ArgentinaDatos**: proyectos comunitarios gratuitos que recopilan cotizaciones del
  dólar (incluido el blue, que no tiene fuente oficial) y el riesgo país. Una cotización de 0 se
  trata como "sin dato".
- **SEC EDGAR**: gratis, pero la SEC exige identificarse con nombre y email (`SEC_USER_AGENT`);
  sin eso la sección Empresas queda deshabilitada. Sólo cubre empresas que presentan balances
  en EE.UU. (no ADRs que presentan 20-F, como GGAL o YPF). Cuando la empresa sólo reporta el
  año completo, el 4.º trimestre se calcula como anual − 3 trimestres y se marca con `*`. La
  reacción del precio usa los 8-K con ítem 2.02 y la hora de aceptación de la SEC para saber si
  se publicó antes, durante o después de la rueda. No incluye la sorpresa frente al consenso de
  analistas: no hay una fuente gratuita y confiable de estimados.
- **Forense**: sale de los 10-K en XBRL. Si falta un dato, el indicador queda "—" y se listan los
  faltantes; los pocos supuestos (0 de deuda corriente no reportada, 21 % de impuesto si la tasa
  efectiva no se puede calcular, la prima de riesgo) se muestran al lado del resultado. El P/E
  histórico corrige la cantidad de acciones por splits y en cada fecha usa sólo los balances ya
  presentados.
- **Técnico**: el AVWAP usa velas de 1 hora (Yahoo da 2 años) y el precio típico
  (máx + mín + cierre) / 3, la aproximación estándar sin datos operación por operación. El perfil
  de volumen reparte el volumen de cada vela de 30 minutos entre los precios que recorrió. El
  order flow (absorción en el libro de órdenes) no se incluye: requiere datos pagos.
- **ForexFactory**: feed público del calendario de la semana con el pronóstico de consenso. No
  trae el dato real: el agente guarda el pronóstico y el dato real sale de FRED, calculado desde
  la serie oficial (puede diferir en ±0,1 del redondeo publicado). El PBoC y los PMIs no se
  incluyen: no tienen una fuente gratuita confiable.
- **Yahoo Finance**: endpoint público no oficial; puede limitar pedidos o cambiar sin aviso.
  Si falla, la app lo informa como "fuente no disponible". Los precios se cachean 15 minutos.
  Acepta acciones de EE.UU., ADRs, ETFs, índices (`^GSPC`, `^MERV`), acciones de BYMA
  (`GGAL.BA`) y cripto (`BTC-USD`).

## Próximos pasos

1. **Panel diario de índices** globales con un resumen narrativo.
