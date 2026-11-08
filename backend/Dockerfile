FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
COPY scripts ./scripts

# Usuario sin privilegios; la base SQLite vive en /app/data (volumen).
RUN useradd --create-home appuser && mkdir -p /app/data && chown appuser /app/data
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
