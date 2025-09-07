# --- Stage 1: The "Builder" Stage ---
# This stage's only job is to install the Python packages into a clean directory.
# We use a standard python image which has all the necessary build tools.
FROM python:3.9 as builder

# Set the working directory for our package installation
WORKDIR /packages

# Copy only the requirements file first to leverage Docker's layer caching.
# This layer will only be rebuilt if requirements.txt changes.
COPY requirements.txt .

# Install all packages from requirements.txt into the current directory (/packages).
# The '-t .' or '--target .' flag is the key to creating a self-contained bundle.
RUN pip install --no-cache-dir -r requirements.txt -t .


# --- Stage 2: The Final "Runtime" Stage ---
# This is the lean, final image that will be deployed.
# We start from the same slim base image you were using.
FROM python:3.9-slim

# Set the final working directory for the function.
WORKDIR /function

# Install ONLY the runtime OS dependency for psycopg2.
# This is required in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy the pre-installed packages from the "builder" stage.
# This is the magic of multi-stage builds. We take only the finished product.
COPY --from=builder /packages /function

# Copy your function's source code into the final image.
COPY func.py .

# Use the same entrypoint that was working for you.
ENTRYPOINT ["python", "-m", "fdk", "func", "handler"]
