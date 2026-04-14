FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY backend/  ./backend/
COPY frontend/ ./frontend/
COPY run.py    ./

# Create data dir (real data mounted as volume in prod)
RUN mkdir -p ./data

# Expose port
EXPOSE 8000

# Use run.py entrypoint which correctly sets sys.path
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"]
