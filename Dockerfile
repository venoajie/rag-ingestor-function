
# --- Stage 1: Builder ---
# This stage builds the virtual environment with all dependencies.
FROM python:3.11-bullseye as builder

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
# This stage creates the final, lean production image.
FROM python:3.11-slim-bullseye

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
# Prevents Python from writing .pyc files to disc
ENV PYTHONDONTWRITEBYTECODE=1
# Ensures logs and other output are sent straight to stdout without buffering
ENV PYTHONUNBUFFERED=1

# Expose the port the application will run on
EXPOSE 8080

# The command to run the application using Uvicorn.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]