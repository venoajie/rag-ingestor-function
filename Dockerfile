FROM fnproject/python:3.9-dev as build-stage

WORKDIR /function

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy function code
COPY func.py .

# Test that all imports work
RUN python -c "import func"

FROM fnproject/python:3.9

WORKDIR /function

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages and function from build stage
COPY --from=build-stage /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
COPY --from=build-stage /function/func.py .

ENV PYTHONPATH=/function

ENTRYPOINT ["fdk", "/function/func.py", "handler"]
