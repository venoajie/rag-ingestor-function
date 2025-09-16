
# --- Stage 1: The "Builder" Stage ---
# Use a modern, stable Python version.
FROM python:3.11-slim as builder

WORKDIR /packages
COPY requirements.txt .
# Install packages into a self-contained bundle.
RUN pip install --no-cache-dir -r requirements.txt -t .

# --- Stage 2: The Final "Runtime" Stage ---
# We can use the same slim base image.
FROM python:3.11-slim

WORKDIR /function

# BEST PRACTICE: With psycopg (v3), we no longer need to install libpq5 via apt-get.
# This makes the image smaller and the build faster and more reliable.

# Copy the pre-installed packages from the "builder" stage.
COPY --from=builder /packages /function

# Copy your function's source code.
COPY func.py .
