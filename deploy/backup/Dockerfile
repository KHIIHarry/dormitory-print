# Encrypted backup/restore tool image.  PostgreSQL client version matches the
# postgres:16 service so pg_dump/pg_restore are compatible.
FROM postgres:16-alpine

ENV PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    RESTIC_CACHE_DIR=/state/restic-cache

RUN apk add --no-cache \
        bash \
        coreutils \
        findutils \
        util-linux \
        restic \
        python3 \
        tini \
        ca-certificates \
        tzdata \
        openssh-client \
    && addgroup -g 10001 -S dormprint \
    && adduser -u 10001 -S -G dormprint -h /opt/dormprint dormprint \
    && mkdir -p /opt/dormprint /state /storage \
    && chown -R 10001:10001 /opt/dormprint /state /storage

WORKDIR /opt/dormprint

COPY --chown=10001:10001 lib.sh backup.sh backup-scheduler.sh restore.sh \
    check_capacity.sh verify_manifest.py apply_tombstones.py post_restore.sql \
    /opt/dormprint/
RUN chmod 0755 /opt/dormprint/*.sh /opt/dormprint/*.py \
    && chmod 0644 /opt/dormprint/lib.sh /opt/dormprint/post_restore.sql

USER 10001:10001

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/opt/dormprint/backup-scheduler.sh"]
