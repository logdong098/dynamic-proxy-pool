FROM python:3.11-slim

WORKDIR /app

# Install basic networking and compile tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Ensure data directory exists
RUN mkdir -p /app/data

# Expose FastAPI port
EXPOSE 18081

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:18081/api/v1/stats || exit 1

# Launch main application
CMD ["python", "main.py"]
