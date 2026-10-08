FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PATH="/app/backend/.venv/bin:$PATH"
WORKDIR /app/backend
ARG INSTALL_VISUAL=false
RUN pip install --no-cache-dir uv==0.12.13
COPY backend/pyproject.toml backend/uv.lock ./
RUN if [ "$INSTALL_VISUAL" = true ]; then uv sync --frozen --no-dev --no-install-project --extra visual; else uv sync --frozen --no-dev --no-install-project; fi
COPY backend/src ./src
RUN if [ "$INSTALL_VISUAL" = true ]; then uv sync --frozen --no-dev --no-editable --extra visual; else uv sync --frozen --no-dev --no-editable; fi
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
RUN useradd --create-home dropgrid
RUN mkdir -p /app/media && chown dropgrid:dropgrid /app/media
USER dropgrid
CMD ["python", "-m", "dropgrid.api"]
