
# --- Stage 1: Base with UV and a Virtual Environment ---
# Use a modern, stable Python version.
FROM python:3.11-slim AS base

# Create a non-root user for better security, similar to the Librarian.
# The FDK runtime will use this user.
RUN useradd --create-home --shell /bin/bash appuser

# Set up the virtual environment using uv.
ENV UV_VENV=/opt/venv
RUN python -m pip install --no-cache-dir uv \
    && python -m uv venv ${UV_VENV} --python python3.11 \
    && chown -R appuser:appuser ${UV_VENV}

# --- Stage 2: Builder - Install Dependencies ---
# This stage installs the Python packages into the virtual environment.
FROM base AS builder

# Activate the virtual environment for subsequent commands.
ENV PATH="${UV_VENV}/bin:$PATH"

WORKDIR /function
COPY requirements.txt .

# Use uv to install dependencies into the virtual environment created in the base stage.
RUN uv pip install --no-cache-dir -r requirements.txt

# --- Stage 3: Runtime - The Final, Lean Image ---
FROM base AS runtime

WORKDIR /function

# Copy the populated virtual environment from the builder stage.
COPY --from=builder ${UV_VENV} ${UV_VENV}

# Copy your function's source code.
COPY func.py .

# Set ownership for the function code.
RUN chown -R appuser:appuser /function

# Switch to the non-root user.
USER appuser

# This is the standard, required entrypoint for the Python FDK.
# It uses the Python executable from WITHIN the virtual environment.
ENTRYPOINT ["/opt/venv/bin/python", "-m", "fdk", "func.py", "handler"]
