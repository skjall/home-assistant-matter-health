ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.14-alpine3.22

# The page is plain JavaScript once built, so it is built once on the build
# machine's own architecture; running node under emulation for another
# architecture is slow and known to crash.
FROM --platform=$BUILDPLATFORM node:24-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM $BUILD_FROM

# The layout mirrors the repository, so the web app finds the translations
# relative to its own file in both places.
WORKDIR /opt/matter_health
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY translations/ ./translations/
COPY --from=frontend /build/app/matter_health/web/static/ ./app/matter_health/web/static/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

ENV PYTHONPATH=/opt/matter_health/app \
    PYTHONUNBUFFERED=1

CMD [ "/run.sh" ]
