# --- Stage 1: The "Builder" Stage ---
# Use a more modern Python version. 3.11 is a good, stable choice.
FROM python:3.11 as builder

WORKDIR /packages
COPY requirements.txt .
# Install packages into a self-contained bundle.
RUN pip install --no-cache-dir -r requirements.txt -t .

# --- Stage 2: The Final "Runtime" Stage ---
FROM python:3.11-slim

WORKDIR /function

# Install the runtime OS dependency for psycopg2.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy the pre-installed packages from the "builder" stage.
COPY --from=builder /packages /function

# Copy your function's source code.
COPY func.py .
