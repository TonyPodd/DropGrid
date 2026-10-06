FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PATH="/app/backend/.venv/bin:$PATH"
WORKDIR /app/backend
RUN pip install --no-cache-dir uv==0.12.13
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev --no-editable
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
RUN useradd --create-home dropgrid
USER dropgrid
CMD ["python", "-m", "dropgrid.api"]
