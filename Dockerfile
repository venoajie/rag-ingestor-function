
# Dockerfile
# --- Stage 1: Builder ---
# Using Python 3.12-slim. No venv is created here.
FROM python:3.12-slim-bookworm AS builder

# Install build essentials for C extensions.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install dependencies directly into the system site-packages.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --- Stage 2: Runtime ---
# Using the same base image ensures compatibility.
FROM python:3.12-slim-bookworm

# Create a non-root user for running the application.
RUN useradd --system --create-home --shell /bin/bash appuser
WORKDIR /function

# Copy the installed packages from the builder's system site-packages
# to the runtime's system site-packages.
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Copy the executables (like uvicorn) from the builder's system bin
# to the runtime's system bin.
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy the application code.
COPY main.py .

# Set correct ownership.
RUN chown -R appuser:appuser /function

# Switch to the non-root user.
USER appuser

# Set environment variables. The PATH is already correct by default.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# The CMD can now be simple, as uvicorn is in the system PATH.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "uvloop"]