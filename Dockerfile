
# --- Stage 1: The Builder Stage ---
# Use Python 3.11 on Debian "Bullseye". This older, highly common base OS
# has better binary compatibility with the pre-compiled wheels available on PyPI
# for older packages like httptools==0.4.0. This avoids compilation entirely.
FROM python:3.11-bullseye as builder

# Create and activate the virtual environment.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install the Python dependencies.
# Pip should now find a compatible pre-compiled wheel for httptools,
# making gcc and build-essential unnecessary.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# --- Stage 2: The Final Runtime Stage ---
# Use the corresponding slim image for the final, lean container.
FROM python:3.11-slim-bullseye

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