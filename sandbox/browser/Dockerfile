FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN pip install --no-cache-dir playwright==1.49.1
COPY entrypoint.py /opt/trustscout/entrypoint.py
USER pwuser
ENTRYPOINT ["python", "-u", "/opt/trustscout/entrypoint.py"]
