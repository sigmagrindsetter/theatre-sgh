# Recruitment review panel (REVIEW_PANEL_SPEC.md §6.3). Build context = repo root:
#   docker build -f apps/recruitment/Dockerfile .
FROM python:3.11-slim

ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
WORKDIR /app

COPY apps/recruitment/requirements.txt apps/recruitment/requirements.txt
RUN pip install --no-cache-dir -r apps/recruitment/requirements.txt

RUN useradd --system --uid 10001 panel

COPY shared/ shared/
COPY apps/recruitment/ apps/recruitment/

USER panel
WORKDIR /app/apps/recruitment
# Cloud Run's front end terminates TLS and sets X-Forwarded-*; trust it so URLs are https
CMD ["sh", "-c", "exec uvicorn panel.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips '*'"]
