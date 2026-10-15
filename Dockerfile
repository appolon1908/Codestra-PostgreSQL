FROM postgres:17-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates age \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir /backups /catalog && chown postgres:postgres /backups /catalog \
    && chmod 0700 /catalog
WORKDIR /app
COPY management /app/management
COPY scripts /app/scripts
USER postgres
ENTRYPOINT ["python3"]
CMD ["-m", "management.server"]
