# Usamos una imagen de Python ligera estable
FROM python:3.11-slim

# Evita que Python escriba archivos .pyc y fuerza que la salida de consola sea inmediata
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Actualizar repositorios e instalar dependencias por separado para evitar fallos de caché (Exit Code 100)
RUN apt-get update --fix-missing && apt-get upgrade -y

# Instalar dependencias esenciales del sistema operativo
RUN apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-spa \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Configurar el directorio de trabajo
WORKDIR /app

# Copiar e instalar requerimientos de Python primero (aprovecha la caché de Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código del servidor
COPY . .

# Exponer el puerto por defecto de Render
EXPOSE 10000

# Comando de arranque optimizado para la memoria RAM de Render Free
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "2", "--max-requests", "50", "--preload"]
