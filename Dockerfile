FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIK_DATABASE_URL=sqlite:////data/pik.db \
    PIK_SEED_DEMO=1

RUN useradd --create-home --uid 10001 pik
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY docs ./docs
RUN pip install --no-cache-dir . \
    && mkdir -p /data \
    && chown -R pik:pik /app /data
USER pik
EXPOSE 8400
CMD ["uvicorn", "pik_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8400"]
