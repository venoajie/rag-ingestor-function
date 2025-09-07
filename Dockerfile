FROM python:3.9-slim

WORKDIR /function

# Only need libpq for runtime
RUN apt-get update && apt-get install -y \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir fdk>=0.1.50
RUN pip install --no-cache-dir -r requirements.txt

# Copy function code
COPY func.py .

ENTRYPOINT ["python", "-m", "fdk", "func", "handler"]