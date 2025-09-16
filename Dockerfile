
# --- Stage 1: The "Builder" Stage ---
# Use a modern, stable Python version.
FROM python:3.11-slim AS builder

WORKDIR /packages
COPY requirements.txt .
# Install packages into a self-contained bundle.
RUN pip install --no-cache-dir -r requirements.txt -t .

# --- Stage 2: The Final "Runtime" Stage ---
# We can use the same slim base image.
FROM python:3.11-slim

WORKDIR /function

# Copy the pre-installed packages from the "builder" stage.
COPY --from=builder /packages /function

# Copy your function's source code.
COPY func.py .
