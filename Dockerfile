
# Stage 1: A minimal stage that provides the 'uv' binary for the target architecture.
FROM ghcr.io/astral-sh/uv:latest AS uv

# Stage 2: The Builder stage, using a standard Python base image.
FROM python:3.11-slim AS builder

# BEST PRACTICE: Enable bytecode compilation for faster cold starts.
ENV UV_COMPILE_BYTECODE=1
# BEST PRACTICE: Create a deterministic layer by disabling installer metadata.
ENV UV_NO_INSTALLER_METADATA=1

WORKDIR /function

COPY requirements.txt .

# CRITICAL FIX: Mount the native 'uv' binary from the 'uv' stage and use it.
# This avoids the QEMU segmentation fault.
# We also mount a cache directory to speed up subsequent builds.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=from=uv,source=/uv,target=/bin/uv \
    uv pip install --no-cache-dir -r requirements.txt -t .

# Stage 3: The Runtime stage, the final lean image.
FROM python:3.11-slim

WORKDIR /function

# Create a non-root user for security.
RUN useradd --create-home --shell /bin/bash appuser

# Copy the pre-installed and pre-compiled dependencies from the builder stage.
COPY --from=builder /function .

# Copy the function's source code.
COPY func.py .

# Set ownership for all function files.
RUN chown -R appuser:appuser /function

# Switch to the non-root user.
USER appuser

# The standard, required entrypoint for the Python FDK.
ENTRYPOINT ["/usr/local/bin/python", "-m", "fdk", "func.py", "handler"]