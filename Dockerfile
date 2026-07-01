# Usamos una imagen de Python ligera para mantener el consumo de RAM bajo
FROM python:3.11-slim

# Instalar dependencias del sistema operativo (Tesseract y dependencias de OpenCV)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-spa \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Configurar el directorio de trabajo
WORKDIR /app

# Copiar e instalar requerimientos de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código del servidor
COPY . .

# Exponer el puerto por defecto de Render
EXPOSE 10000

# Comando de arranque optimizado para controlar el consumo de memoria en la capa Free
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "2", "--max-requests", "50", "--preload"]
