FROM python:3.12-slim

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin tsproxy
COPY proxy.py /opt/trustscout/proxy.py
USER tsproxy
EXPOSE 8888
ENTRYPOINT ["python", "/opt/trustscout/proxy.py"]
