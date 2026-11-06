# Market Analyst

Sitio personal de análisis de mercado: indicadores macroeconómicos por país y análisis de
activos contra el S&P 500 (beta, correlación, volatilidad, caídas). Todo con fuentes
**gratuitas y sin clave de API**.

| Sección | Qué muestra | Fuente |
|---|---|---|
| **Panorama** | EE.UU. de un vistazo (Fed, 10 años, pendiente, inflación, desempleo, VIX) y último dato anual de PBI, crecimiento, PBI per cápita, inflación, desempleo, cuenta corriente y deuda, por país | Banco Mundial |
| **EE.UU.** | Fed, bonos del Tesoro (3M a 30A), pendientes 10A−2A y 10A−3M, hipotecas, inflación (IPC, núcleo, PCE núcleo, esperada), desempleo, empleos creados, pedidos de desempleo, PBI, VIX, spread high yield, dólar y petróleo. Historia de cada serie y curva de rendimientos hoy / hace 1 mes / hace 1 año | FRED (Reserva Federal de St. Louis) |
| **Argentina** | Dólar oficial, mayorista, MEP, CCL, blue, cripto y tarjeta con su brecha; evolución de oficial/MEP/CCL/blue; riesgo país; reservas, base monetaria, tasa de política, BADLAR, TAMAR, plazo fijo, inflación mensual/interanual/esperada, CER y UVA | BCRA (oficial), DolarApi y ArgentinaDatos (comunitarias) |
| **Empresas** | Últimos 12 trimestres: ingresos, variación anual, márgenes bruto/operativo/neto, ganancia neta y por acción; reacción de la acción (y exceso sobre el SPY) a 1, 5 y 20 ruedas de cada publicación de resultados | SEC EDGAR + Yahoo Finance |
| **Macro global** | Series históricas por indicador, comparando hasta 8 países (EE.UU., Canadá, Argentina, Brasil, México, Chile, Zona Euro, Alemania, Francia, Italia, Reino Unido, Japón, China) | Banco Mundial |
| **Mercados** | Beta, correlación, R², rendimiento, volatilidad anualizada y caída máxima contra un benchmark (SPY, QQQ, IWM, EEM, ACWI); correlación y beta móviles; comparación de hasta 15 activos | Yahoo Finance |

## Principio: cero dato inventado

Heredado del proyecto Financiero. Si la fuente no trae un dato, se muestra "—" con su motivo;
nunca un `0` ni un valor interpolado. Cada número muestra la fuente, la hora de la consulta y,
en los datos macro, el año al que corresponde. Con menos de 60 ruedas en común, beta y
correlación quedan como "no disponible" en lugar de mostrar una cifra poco confiable.

## Estructura

```
backend/    API en Python (FastAPI)
  app/sources/    clientes de Banco Mundial, FRED, BCRA, DolarApi, ArgentinaDatos,
                  SEC EDGAR y Yahoo Finance
  app/services/   macro.py (países), us_macro.py (FRED), ar_macro.py (Argentina),
                  earnings.py (SEC), quant.py (fórmulas), markets.py
  app/api/        endpoints /api/auth, /api/macro, /api/us, /api/ar, /api/markets,
                  /api/earnings
  tests/          tests con respuestas simuladas (no salen a la red)
frontend/   sitio en Next.js (App Router) + Recharts
```

El navegador sólo habla con Next.js; `next.config.ts` reenvía `/api/*` al backend. Así la
cookie de sesión (httpOnly) queda en el mismo dominio y no hace falta CORS.

## Cómo correrlo en tu máquina

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
- **Yahoo Finance**: endpoint público no oficial; puede limitar pedidos o cambiar sin aviso.
  Si falla, la app lo informa como "fuente no disponible". Los precios se cachean 15 minutos.
  Acepta acciones de EE.UU., ADRs, ETFs, índices (`^GSPC`, `^MERV`), acciones de BYMA
  (`GGAL.BA`) y cripto (`BTC-USD`).

## Próximos pasos

1. **Panel diario de índices** globales con un resumen narrativo.
