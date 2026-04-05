FROM python:3.11-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY autoagentstudioapp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files
COPY . .

# Set working directory to the app directory
WORKDIR /app/autoagentstudioapp

EXPOSE 8000

# Run uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
