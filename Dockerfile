# Stage 1: Builder - Installs dependencies
FROM fnproject/python:3.9-dev as builder

WORKDIR /function

# Install OS-level dependencies needed by psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /function/
RUN pip install --no-cache-dir -r requirements.txt -t /function/

# Stage 2: Final Image - Copies code and dependencies
FROM fnproject/python:3.9-slim

WORKDIR /function

# Copy installed dependencies from the builder stage
COPY --from=builder /function/ /function/

# Copy the function code
COPY func.py /function/

# Define the entrypoint for the function
ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]