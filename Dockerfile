
# --- Stage 1: The Builder Stage ---
# Use a full-featured base image that includes build tools like gcc.
# This allows pip to compile C extensions like 'httptools' from source.
FROM python:3.11-bookworm AS builder

# Install system-level 
# Install system-level build-essential package (provides gcc).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create and activate the virtual environment.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy BOTH requirements files.
COPY requirements.txt build-requirements.txt ./

# --- THE CRITICAL FIX ---
# First, install the build-time dependencies into the venv.
# This ensures a specific, known-good version of Cython is available.
RUN pip install --no-cache-dir -r build-requirements.txt

# Second, install the application dependencies.
# When pip builds httptools, it will now use the pre-installed, correct Cython version.
RUN pip install --no-cache-dir -r requirements.txt


# --- Stage 2: The Final Runtime Stage ---
# Use the corresponding slim image for the final, lean container.
FROM python:3.11-slim-bookworm

WORKDIR /function

# Create a non-root user for security.
RUN useradd --system --create-home --shell /bin/bash appuser

# Copy the fully populated virtual environment from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy the function's source code.
COPY func.py .

# Pre-compile all Python files to bytecode for cold start optimization.
RUN /opt/venv/bin/python -m compileall -j 0 /opt/venv /function

# Set ownership for all function files to the non-root user.
RUN chown -R appuser:appuser /function /opt/venv

# Switch to the non-root user.
USER appuser

# Set the environment to use the venv's Python interpreter.
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Define the entrypoint for the OCI Functions runtime.
ENTRYPOINT ["fdk", "func.py", "handler"]