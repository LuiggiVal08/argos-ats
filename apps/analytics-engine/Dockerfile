FROM python:3.11-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml requirements.txt* ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.11-slim
WORKDIR /app
RUN addgroup --system argos && adduser --system --ingroup argos argos && \
    mkdir -p /var/lib/argos /app/data && chown -R argos:argos /var/lib/argos /app/data
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels argos-analytics-engine && \
    rm -rf /wheels
COPY app/ app/
USER argos
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
