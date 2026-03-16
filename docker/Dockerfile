# Use a lightweight Python base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies if needed (e.g., for Pillow)
RUN apt-get update && apt-get install -y \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create a volume for persistent data
# data.db, tg_session.session, and logs will be stored here
# Suggest users mount a host directory to /app/persistent
# We can adjust the script to look for DB in a better place, 
# but for now, we'll just encourage mapping the files directly.

# Expose the communication port
EXPOSE 8000

# Start the application
CMD ["python", "main.py"]
