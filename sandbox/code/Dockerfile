FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir matplotlib==3.9.2 \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin sandbox

COPY entrypoint.py /opt/trustscout/entrypoint.py
USER sandbox
ENTRYPOINT ["python", "-u", "/opt/trustscout/entrypoint.py"]
