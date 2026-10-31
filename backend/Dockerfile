# DormPrint production image.
#
# The runtime user is a fixed non-root UID.  LibreOffice, Ghostscript, the CJK
# fonts and HEIF/PDF libraries are installed from the distribution so document
# conversion does not depend on a developer workstation.  Python dependencies
# are installed only from the locked backend/requirements.txt.
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    LANG=C.UTF-8

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        tini \
        libreoffice-writer \
        libreoffice-calc \
        libreoffice-impress \
        libreoffice-core \
        ghostscript \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        fonts-noto-color-emoji \
        fonts-dejavu-core \
        fonts-liberation \
        libheif1 \
        libheif-examples \
        libde265-0 \
        libjpeg62-turbo \
        libpng16-16 \
        libtiff6 \
        libwebp7 \
        libopenjp2-7 \
        libxml2 \
        libxslt1.1 \
        libfreetype6 \
        shared-mime-info \
        fontconfig \
    && fc-cache -f \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --no-compile -r requirements.txt

# .dockerignore excludes virtualenvs, local data, test suites and secrets.
COPY . /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home \
        --shell /usr/sbin/nologin app \
    && mkdir -p /var/lib/dormprint \
    && chown -R 10001:10001 /app /var/lib/dormprint \
    && chmod 0700 /var/lib/dormprint

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
