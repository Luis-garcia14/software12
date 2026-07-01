#!/usr/bin/env bash
# Salir inmediatamente si ocurre un error
set -o errexit

# 1. Actualizar los paquetes e instalar Tesseract OCR con su idioma español
echo "📦 Instalando Tesseract OCR en el sistema operativo..."
apt-get update && apt-get install -y tesseract-ocr tesseract-ocr-spa

# 2. Instalar las librerías normales de Python (Flask, OpenCV, etc.)
echo "🐍 Instalando dependencias de Python desde requirements.txt..."
pip install -r requirements.txt

echo "✅ ¡Compilación e instalación completada con éxito!"
