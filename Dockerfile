# --- Stage 1: Builder ---
# Use a specific, stable version of the base image for reproducibility.
FROM python:3.12.3-slim-bookworm AS builder

# Set an environment variable for the venv path.
ENV VENV_PATH=/opt/venv

# Install build essentials for C extensions that some packages might need.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create the virtual environment.
RUN python3 -m venv $VENV_PATH

# Activate the venv for subsequent commands in this stage.
ENV PATH="$VENV_PATH/bin:$PATH"

# Upgrade pip within the venv.
RUN pip install --no-cache-dir --upgrade pip

# Copy only requirements to leverage layer caching.
COPY requirements.txt .

# Install dependencies into the venv.
RUN pip install --no-cache-dir -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.12.3-slim-bookworm

# Set the same venv path environment variable.
ENV VENV_PATH=/opt/venv

# Set the PATH to use the venv's executables.
# This ensures 'uvicorn' and other commands are found.
ENV PATH="$VENV_PATH/bin:$PATH"

# Create a non-root user for security.
RUN useradd --system --create-home --shell /bin/bash appuser
WORKDIR /function

# Copy the entire virtual environment from the builder stage.
COPY --from=builder $VENV_PATH $VENV_PATH

# Copy the application code.
COPY main.py .

# Set correct ownership for the entire function directory.
RUN chown -R appuser:appuser /function

# Switch to the non-root user.
USER appuser

# Set standard Python environment variables for containers.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# The CMD is now simple and robust. 'uvicorn' is found via the PATH.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "uvloop", "--workers", "1"]