# Market Analyst

Sitio personal de análisis de mercado: indicadores macroeconómicos por país y análisis de
activos contra el S&P 500 (beta, correlación, volatilidad, caídas). Todo con fuentes
**gratuitas y sin clave de API**.

| Sección | Qué muestra | Fuente |
|---|---|---|
| **Panorama** | EE.UU. de un vistazo (Fed, 10 años, pendiente, inflación, desempleo, VIX) y último dato anual de PBI, crecimiento, PBI per cápita, inflación, desempleo, cuenta corriente y deuda, por país | Banco Mundial |
| **EE.UU.** | Fed, bonos del Tesoro (3M a 30A), pendientes 10A−2A y 10A−3M, hipotecas, inflación (IPC, núcleo, PCE núcleo, esperada), desempleo, empleos creados, pedidos de desempleo, PBI, VIX, spread high yield, dólar y petróleo. Historia de cada serie y curva de rendimientos hoy / hace 1 mes / hace 1 año | FRED (Reserva Federal de St. Louis) |
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
  app/sources/    clientes de Banco Mundial, FRED y Yahoo Finance
  app/services/   macro.py (países), us_macro.py (FRED), quant.py (fórmulas), markets.py
  app/api/        endpoints /api/auth, /api/macro, /api/us, /api/markets
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
- **Yahoo Finance**: endpoint público no oficial; puede limitar pedidos o cambiar sin aviso.
  Si falla, la app lo informa como "fuente no disponible". Los precios se cachean 15 minutos.
  Acepta acciones de EE.UU., ADRs, ETFs, índices (`^GSPC`, `^MERV`), acciones de BYMA
  (`GGAL.BA`) y cripto (`BTC-USD`).

## Próximos pasos

1. **BCRA** (sin clave): reservas, tipo de cambio, tasas y base monetaria de Argentina.
2. **Resultados trimestrales**: sorpresa frente al estimado y reacción del precio.
3. **Panel diario de índices** globales con un resumen narrativo.
