FROM python:3.11-slim

# Install ffmpeg and streamlink dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip install streamlink requests

# Copy app
WORKDIR /app
COPY tv_cloud.py .

# Expose port
EXPOSE 8080

CMD ["python", "-u", "tv_cloud.py"]
