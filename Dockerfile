
# Stage 1: The Builder stage, using a standard Python base image.
# We don't need a separate 'uv' stage if we are using pip.
FROM python:3.11-slim AS builder

WORKDIR /function

COPY requirements.txt .

# Use the standard 'pip install --target' which is what the OCI FDK expects.
# This installs all packages directly into the current directory.
RUN pip install --no-cache-dir -r requirements.txt -t .

# Stage 2: The Runtime stage, the final lean image.
FROM python:3.11-slim

WORKDIR /function

# Create a non-root user for security.
RUN useradd --create-home --shell /bin/bash appuser

# Copy the pre-installed dependencies from the builder stage.
COPY --from=builder /function .

# Copy the function's source code.
COPY func.py .

# Set ownership for all function files.
RUN chown -R appuser:appuser /function

# Switch to the non-root user.
USER appuser

# The standard, required entrypoint for the Python FDK.
ENTRYPOINT ["/usr/local/bin/python", "-m", "fdk", "func.py", "handler"]