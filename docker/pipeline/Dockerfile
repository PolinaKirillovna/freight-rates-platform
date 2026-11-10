FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY common ./common
COPY etl ./etl
COPY ml ./ml
COPY configs ./configs
COPY sql ./sql
COPY tests ./tests
COPY pyproject.toml ./

RUN useradd --create-home --uid 1000 pipeline && mkdir -p /app/artifacts && chown pipeline /app/artifacts
USER pipeline

CMD ["python", "-m", "etl.cli", "all"]
