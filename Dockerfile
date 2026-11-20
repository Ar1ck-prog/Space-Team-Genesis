FROM python:3.11-slim

WORKDIR /app
COPY . /app
ENV PYTHONPATH=/app:/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /app/outputs && chown 10001:10001 /app/outputs
USER 10001

CMD ["python", "-m", "space_team_genesis.cli", "serve", "--host", "0.0.0.0"]
