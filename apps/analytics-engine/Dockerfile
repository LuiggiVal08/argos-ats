FROM python:3.11-slim

# Build arg to include optional ML extras (tensorflow, xgboost, scikit-learn, torch).
# NOTE: [ml] extras are heavy (~600MB tensorflow). For production we install
# only scikit-learn (the actual runtime dependency). If you need the full stack
# (xgboost, torch, tensorflow), pass --build-arg PIP_EXTRAS=ml.
ARG PIP_EXTRAS=

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create user early so we can chown /app/data
RUN addgroup --system argos && adduser --system --ingroup argos argos && \
    mkdir -p /var/lib/argos /app/data && chown -R argos:argos /var/lib/argos /app/data

# Install all Python dependencies in one cached layer
COPY apps/analytics-engine/pyproject.toml apps/analytics-engine/requirements.txt* ./
RUN if [ -n "$PIP_EXTRAS" ]; then \
        pip install --no-cache-dir ".[${PIP_EXTRAS}]"; \
    else \
        pip install --no-cache-dir . && \
        pip install --no-cache-dir scikit-learn; \
    fi

# Shared contracts (needed at import time by contracts/registry.py)
COPY contracts/ contracts/

# App source overlay
COPY apps/analytics-engine/app/ app/

# Strip build-only packages from final image
RUN apt-get purge -y --auto-remove gcc g++ && rm -rf /var/lib/apt/lists/*

USER argos
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
