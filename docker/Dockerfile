# Use PyTorch base image with CUDA 12.4 support
# This image includes PyTorch 2.5.1, CUDA 12.4, and cuDNN 9
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
# We need ffmpeg for audio processing, git for installing some pip packages,
# and build-essential/libsndfile1 for audio libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    libsndfile1 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
# We filter out torch and torchaudio because they are already included in the base image.
# This saves massive amounts of space and download time.
RUN grep -vE "^torch|^torchaudio" requirements.txt > requirements.docker.txt && \
    pip install --no-cache-dir -r requirements.docker.txt

# Copy the rest of the application code
COPY . .

# Expose the port the app runs on
EXPOSE 8000

# Default command (can be overridden in docker-compose)
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
