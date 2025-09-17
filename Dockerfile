
# --- Stage 1: The Builder Stage ---
# This stage creates the virtual environment and installs dependencies into it.
FROM python:3.11-slim AS builder

# Create the virtual environment.
RUN python -m venv /opt/venv

# Activate the venv and install dependencies. This ensures pip is using the venv.
COPY requirements.txt .
RUN . /opt/venv/bin/activate && pip install --no-cache-dir -r requirements.txt

# --- Stage 2: The Runtime Stage ---
# This is the final, lean image.
FROM python:3.11-slim

WORKDIR /function

# Create a non-root user for security.
RUN useradd --create-home --shell /bin/bash appuser

# Copy the entire virtual environment from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy the function's source code.
COPY func.py .

# Set ownership for all function files.
RUN chown -R appuser:appuser /function

# Switch to the non-root user.
USER appuser

# The standard, required entrypoint for the Python FDK.
# CRITICAL: It now uses the Python executable from WITHIN the virtual environment.
ENTRYPOINT ["/opt/venv/bin/python", "-m", "fdk", "func.py", "handler"]