
# --- Stage 1: The Builder Stage ---
# Use a full-featured base image that includes build tools like gcc.
# This allows pip to compile C extensions like 'httptools' from source.
FROM python:3.12-slim AS builder

# Install system-level build dependencies required for compiling some Python packages.
# 'build-essential' provides gcc, make, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment in a standard location.
RUN python3 -m venv /opt/venv

# Set the PATH to use the venv's binaries for subsequent commands.
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install the Python dependencies into the venv.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# --- Stage 2: The Final Runtime Stage ---
# Use a minimal, secure base image for the final application.
FROM python:3.12-slim

# Set the working directory for the function code.
WORKDIR /function

# Create a non-root user for security, as per best practice.
# Using --system creates a user without a password, which is ideal for containers.
RUN useradd --system --create-home --shell /bin/bash appuser

# Copy the entire virtual environment, with all its pre-installed packages,
# from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy the function's source code into the working directory.
COPY func.py .

# --- COLD START OPTIMIZATION ---
# Pre-compile all Python files to bytecode to slightly improve startup time.
# This compiles both the venv libraries and our function code.
RUN /opt/venv/bin/python -m compileall -j 0 /opt/venv /function

# Set ownership for all function files to the non-root user.
# This must be done *before* switching the user.
RUN chown -R appuser:appuser /function /opt/venv

# Switch to the non-root user for the remainder of the build and at runtime.
USER appuser

# Set the environment to use the venv's Python interpreter at runtime.
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Define the entrypoint for the OCI Functions runtime, using the venv's FDK.
ENTRYPOINT ["fdk", "func.py", "handler"]