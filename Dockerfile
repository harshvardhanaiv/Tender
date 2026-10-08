# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8092

# Default database environment variables
ENV DB_HOST=localhost
ENV DB_PORT=5440
ENV DB_NAME=postgres
ENV DB_USER=postgres
ENV DB_PASSWORD="root"

# Install system dependencies required for ddddocr and lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir --default-timeout=1000 -r requirements.txt

# Copy the current directory contents into the container at /app
COPY . /app/

# Expose the port the app runs on
EXPOSE 8092

# Command to run the application
CMD ["python", "server.py"]
