
# --- Stage 1: Builder ---
# Use a specific, stable version of the base image for reproducibility.
FROM python:3.12.3-slim-bookworm AS builder

# Set an environment variable for the venv path.
ENV VENV_PATH=/opt/venv

# --- THE UV UPGRADE (Corrected Method) ---
# Install uv using pip into the global site-packages. This is the most
# reliable method inside a Docker build.
RUN pip install --no-cache-dir uv
# Verify the installation. 'uv' will now be on the default PATH.
RUN uv --version
# --- END UV UPGRADE ---

# Create the virtual environment.
RUN python3 -m venv $VENV_PATH

# Activate the venv for subsequent commands in this stage.
ENV PATH="$VENV_PATH/bin:$PATH"

# Copy only requirements to leverage layer caching.
COPY requirements.lock.txt .

# Install dependencies into the venv using the much faster 'uv'.
RUN uv pip install --no-cache -r requirements.lock.txt

# --- Stage 2: Runtime ---
# This stage remains unchanged.
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