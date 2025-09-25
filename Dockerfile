# WARNING: ADVANCED BUILD PATTERN - WHEEL HARVESTING
# Incident ID: PB-20250924-01
# 
# httptools==0.4.0 source code is incompatible with Python 3.11+ C-API.
# This build harvests a pre-compiled wheel from Python 3.10 and force-installs it.

# --- Stage 0: Wheel Harvester ---
FROM python:3.10-bullseye as wheel_harvester
WORKDIR /wheels

# Download the pre-compiled wheel for httptools==0.4.0
# The --only-binary :all: flag ensures we ONLY get wheels, not source distributions
# The --python-version and --platform flags ensure compatibility
RUN pip download \
    --only-binary :all: \
    --platform linux_x86_64 \
    --python-version 310 \
    httptools==0.4.0

# --- Stage 1: Builder ---
FROM python:3.11-bullseye as builder

# Install build essentials (some other packages might need compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy the harvested wheel
COPY --from=wheel_harvester /wheels/httptools*.whl /tmp/

# Force install the Python 3.10 wheel into our Python 3.11 environment
# --force-reinstall ensures it overwrites any existing installation
# --no-deps prevents pip from trying to resolve dependencies (and thus rebuild)
RUN pip install --force-reinstall --no-deps /tmp/httptools*.whl

# Verify the installation succeeded
RUN python -c "import httptools; print(f'✅ httptools {httptools.__version__} force-installed successfully')"

# Now install the rest of the requirements
# httptools is already installed, so pip won't try to build it
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.11-slim-bullseye

WORKDIR /function

RUN useradd --system --create-home --shell /bin/bash appuser

COPY --from=builder /opt/venv /opt/venv
COPY func.py .

RUN /opt/venv/bin/python -m compileall -j 0 /opt/venv /function
RUN chown -R appuser:appuser /function /opt/venv

USER appuser
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["fdk", "func.py", "handler"]