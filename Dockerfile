
# Dockerfile
# --- Stage 1: Builder ---
# UPGRADED: Using Python 3.12-slim on Debian Bookworm for smaller size and modern features.
FROM python:3.12-slim-bookworm AS builder

# Install build essentials as a best practice.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for the build process
RUN useradd --system --create-home builder
USER builder
WORKDIR /home/builder

# Create and activate the virtual environment
RUN python3 -m venv /home/builder/venv
ENV PATH="/home/builder/venv/bin:$PATH"

# Install dependencies into the venv
COPY --chown=builder:builder requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# --- Stage 2: Runtime ---
# UPGRADED: Using Python 3.12-slim for the final runtime image.
FROM python:3.12-slim-bookworm

# Create a non-root user for running the application
RUN useradd --system --create-home --shell /bin/bash appuser
WORKDIR /function

# Copy the virtual environment from the builder stage
COPY --from=builder /home/builder/venv /opt/venv

# Copy the application code
COPY main.py .

# Pre-compile python code for a minor startup performance boost
RUN /opt/venv/bin/python -m compileall -j 0 /opt/venv /function

# Set correct ownership for all application files
RUN chown -R appuser:appuser /function /opt/venv

# Switch to the non-root user
USER appuser

# Set environment variables for the runtime
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Expose the port the application will run on
EXPOSE 8080

# UPGRADED: The CMD now instructs Uvicorn to use the high-performance uvloop event loop.
# This will be overridden by func.yaml, but it's best practice to have it here.
CMD ["/opt/venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "uvloop"]