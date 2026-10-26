# Backend image.
#
# Pinned to an exact Python patch version, not `3.13-slim`: INF-00 requires
# reproducible builds, and a floating tag means the image CI tested is not
# necessarily the image that gets deployed.

FROM python:3.13.0-slim-bookworm

# Fail fast and log immediately. Without PYTHONUNBUFFERED a crash can lose the
# traceback that explains it, because stdout was still buffered.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a source change does not invalidate the install layer.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

# Runs as a non-root user. A container process that does not need to write to
# its own image should not be able to.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Migrations are deliberately NOT run here. INF-00 requires the migration
# runner to be an explicit command an operator can see, not a side effect of
# container start — otherwise a rolling restart of three replicas races three
# migration runs against one database.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
