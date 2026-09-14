# The public agent service.
#
# Installs the repository's own dependencies first, then the web layer, because the
# service imports the real agent rather than a copy of it - agent/, storage/,
# outreach/ and ingestion/ are all live imports. That is the point: what runs for a
# visitor is the same code that runs from the command line, with a different
# database role and a different entry point.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
COPY service/requirements.txt ./service-requirements.txt
RUN pip install --no-cache-dir -r requirements-dev.txt -r service-requirements.txt \
    && apt-get purge -y gcc libpq-dev && apt-get autoremove -y

COPY . .

ENV PYTHONUNBUFFERED=1 SIGNAL_PROFILE=student
EXPOSE 8000

# One worker. The work is IO-bound on two network calls and the whole service
# holds no shared state, so a second worker would buy nothing and double the
# idle database connections.
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
