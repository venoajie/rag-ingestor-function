# Dockerfile

# --- Stage 1: Builder ---

# --- Stage 1: Builder ---
# Use a specific, stable version of the base image for reproducibility.
FROM python:3.12.3-slim-bookworm AS builder

# Set an environment variable for the venv path.
ENV VENV_PATH=/opt/venv

# Install build essentials and curl (for installing uv).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- THE UV UPGRADE ---
ENV UV_HOME=/opt/uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="$UV_HOME/bin:$PATH"
# Verify the installation
RUN uv --version

# Create the virtual environment.
RUN python3 -m venv $VENV_PATH

# Activate the venv for subsequent commands in this stage.
ENV PATH="$VENV_PATH/bin:$PATH"

# Copy only requirements to leverage layer caching.
COPY  requirements.lock.txt .

# Install dependencies into the venv using the much faster 'uv'.
# The '--no-cache' flag is equivalent to pip's '--no-cache-dir'.
RUN uv pip install --no-cache -r  requirements.lock.txt

# --- Stage 2: Runtime ---
# This stage remains unchanged. It doesn't need 'uv', only the final venv.
FROM python:3.12.3-slim-bookworm

ENV VENV_PATH=/opt/venv
ENV PATH="$VENV_PATH/bin:$PATH"

RUN useradd --system --create-home --shell /bin/bash appuser
WORKDIR /function

COPY --from=builder $VENV_PATH $VENV_PATH
COPY main.py .

RUN chown -R appuser:appuser /function
USER appuser

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "uvloop", "--workers", "1"]