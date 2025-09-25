
# WARNING: ADVANCED THREE-STAGE BUILD PATTERN IN USE
# Incident ID: PB-20250924-01
#
# This Dockerfile uses a cross-version "Binary Injection" pattern to work around
# a critical build failure. The core dependency `fdk==0.1.60` requires
# `httptools==0.4.0`, but the source code for this version of httptools is
# fundamentally incompatible with the C-API of Python 3.11 and newer, causing
# a `gcc` compilation error that cannot be fixed by adding build tools.
#
# This build process works by:
# 1. Using a Python 3.10 "Harvester" stage to download a pre-compiled binary wheel
#    of the problematic package.
# 2. Injecting this pre-compiled binary directly into the Python 3.11 "Builder"
#    stage's site-packages.
# 3. Installing the remaining dependencies, which will see that the requirement is
#    already met and skip the broken compilation.
#
# DO NOT attempt to simplify this to a standard two-stage build without
# first verifying that either:
#   a) The `fdk` package has been updated to use a newer, compatible `httptools`, OR
#   b) `httptools==0.4.0` has been patched to support modern Python C-APIs.

# --- Stage 0: The "Harvester" Stage for the problematic dependency ---
# Use an older Python environment where a pre-compiled wheel for httptools==0.4.0 exists.
FROM python:3.10-bullseye as httptools_harvester

# Install only the single problematic package. Pip will find a binary wheel for this environment.
RUN pip install --no-cache-dir httptools==0.4.0


# --- Stage 1: The Main Builder Stage ---
# Use our target Python 3.11 environment.
FROM python:3.11-bullseye as builder

# Create and activate the virtual environment.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# --- BINARY INJECTION ---
# Copy the pre-compiled httptools from the harvester stage directly into our
# target virtual environment's site-packages directory.
COPY --from=httptools_harvester /usr/local/lib/python3.10/site-packages/httptools* /opt/venv/lib/python3.11/site-packages/

# Copy and install the REST of the dependencies.
# Pip will see that httptools==0.4.0 is already present and will NOT attempt to build it.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- ADDED: Sanity Check ---
# Verify that the injected binary is loadable by the Python 3.11 interpreter.
# This provides a fail-fast mechanism if there is an ABI incompatibility.
RUN python -c "import httptools; print(f'✅ Injected httptools {httptools.__version__} loaded successfully in Python 3.11 environment.')"


# --- Stage 2: The Final Runtime Stage ---
# Use the corresponding slim image for the final, lean container.
FROM python:3.11-slim-bullseye

WORKDIR /function

# Create a non-root user for security.
RUN useradd --system --create-home --shell /bin/bash appuser

# Copy the fully populated and surgically-modified virtual environment from the builder stage.
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