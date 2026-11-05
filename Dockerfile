FROM python:3.12-slim

COPY --from=docker:27.4.1-cli /usr/local/bin/docker /usr/local/bin/docker

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/trustscout
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
COPY agent ./agent
COPY sandbox ./sandbox
COPY mockmarket ./mockmarket
RUN pip install --no-cache-dir .

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV TRUSTSCOUT_ROOT=/opt/trustscout
ENV TRUSTSCOUT_DATA_DIR=/var/lib/trustscout
ENV TRUSTSCOUT_HOST_DATA_DIR=/var/lib/trustscout
ENV TRUSTSCOUT_INTERNAL_BASE=http://web:8080
ENV TRUSTSCOUT_APP_NETWORK=trustscout_app
ENV TRUSTSCOUT_PUBLIC_BASE=http://localhost:8080
ENV TRUSTSCOUT_MODE=mock

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
