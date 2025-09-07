FROM fnproject/python:3.9

WORKDIR /function

# Install build and runtime dependencies
RUN apk add --no-cache \
    gcc \
    musl-dev \
    postgresql-dev \
    libpq

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy function code
COPY func.py .

ENTRYPOINT ["fdk", "/function/func.py", "handler"]