# IMPORTACIÓN DE LIBRERÍAS Y MÓDULOS ESENCIALES

from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
import io
import json
import queue
import random
import re
import smtplib
import threading
import time
import mysql.connector
import cv2
import easyocr
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for, send_file
import mysql.connector
import numpy as np
from datetime import datetime, timedelta, timezone
import random
# Librerías de ReportLab para la generación de reportes PDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from collections import Counter
from functools import wraps
from flask import session, redirect, url_for, flash
ultimo_frame_camara = None  # <-- ESTA ES LA QUE TE FALTA DEFINIR GLOBALMENTE
COOLDOWN_PLACAS = {}
RAFAGA_DETECCIONES = []
lock_frame = threading.Lock()
ULTIMAS_DETECCIONES_IA = {}
from functools import wraps

from functools import wraps
from flask import session, redirect, url_for, flash




# 🗄️ CREDENCIALES CENTRALIZADAS DE LA BASE DE DATOS
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'sistema_vehicular'
}
def obtener_conexion():
    return mysql.connector.connect(**DB_CONFIG)

# 📧 CONFIGURACIÓN CENTRALIZADA DEL SERVIDOR DE CORREOS (Gmail SMTP)
CORREO_SENDER = "tucontrolvehicularlg@gmail.com"
CORREO_PASSWORD = "acoqznqjothxnljt"

# 📡 ESTRUCTURAS DE COLAS DE DATOS EN TIEMPO REAL
cola_detecciones = queue.Queue()  # Canal FIFO para Server-Sent Events (SSE)
cola_procesamiento = queue.Queue() # Gestión preventiva de ráfagas vehiculares
ultima_placa_procesada = ""


# INICIALIZACIÓN DEL MOTOR DE INTELIGENCIA ARTIFICIAL (EASYOCR)

try:
    print("[IA SERVER] Cargando modelos de reconocimiento de placas en CPU...")
    # --- CORRECCIÓN: Un solo lector optimizado para evitar fugas de memoria RAM ---
    lector_ocr = easyocr.Reader(['es'], gpu=False) 
    print("[ℹ️ STATUS] Servidor de IA levantado correctamente y listo.")
except Exception as e:
    print(f"❌ CRÍTICO: No se pudo inicializar el motor EasyOCR: {e}")


#  FUNCIONES AUXILIARES Y HERRAMIENTAS DE SOPORTE


# VALIDADOR DE PLACAS: Verifica mediante expresiones 
# regulares que la cadena cumpla con los estándares básicos.
def es_placa_valida(texto):
    patron = r'^[A-Z0-9]{5,8}$'
    return bool(re.match(patron, texto.strip().upper()))

# NOTIFICACIÓN DE BIENVENIDA: Envía de forma automática 
# las credenciales al nuevo operador creado por el Admin.
def enviar_correo_bienvenida(correo_destino, nombre_operador, contrasena):
    msg = MIMEMultipart()
    msg['From'] = CORREO_SENDER
    msg['To'] = correo_destino
    msg['Subject'] = "🔒 Credenciales de Acceso - EJARAD TIC SOLUTIONS"

    cuerpo = f"""
    Estimado(a) {nombre_operador},
    
    Se ha creado exitosamente tu cuenta de operador en la plataforma de Control Vehicular.
    A continuación, se detallan tus credenciales de acceso al sistema:
    
    👤 Usuario/Correo: {correo_destino}
    🔑 Contraseña Temporal: {contrasena}
    
    Por seguridad, se recomienda no compartir estos datos con terceros.
    """
    msg.attach(MIMEText(cuerpo, 'plain'))
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(CORREO_SENDER, CORREO_PASSWORD)
        server.sendmail(CORREO_SENDER, correo_destino, msg.as_string())
        server.quit()
        print(f"✔️ [SMTP] Correo de bienvenida enviado a {correo_destino}")
    except Exception as e:
        print(f"❌ Error SMTP al enviar bienvenida: {e}")
        raise e

# PUENTE SUNARP: Enlace directo que alimenta el motor 
# de búsquedas automáticas con tu diccionario simulado.
def consultar_api_externa_vehiculo(placa):
    placa_limpia = placa.strip().upper()
    if placa_limpia in SUNARP_SIMULADA:
        return SUNARP_SIMULADA[placa_limpia]
    return None


app = Flask(__name__)
app.secret_key = 'ejarad_tic_secret_key_pro_2026'

# =========================================================================
# 1. ESCUDOS DE SEGURIDAD (DECORADORES) Y CONTROL DE CACHÉ
# =========================================================================

@app.after_request
def evitar_cache(response):
    """Destruye la caché para evitar que el botón 'Atrás' del navegador salte la seguridad."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

def login_requerido(f):
    """Filtro de seguridad para Operadores (y Administradores si aplica)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        id_usuario = session.get('usuario_id')
        rol_usuario = str(session.get('rol', '')).lower().strip()
        
        # Deja pasar si hay sesión activa y el rol es operador o administrador
        if not id_usuario or ('operador' not in rol_usuario and 'administrador' not in rol_usuario and 'admin' not in rol_usuario): 
            print("🛑 INTENTO DE ACCESO NO AUTORIZADO - Redirigiendo al login de Operador")
            return redirect(url_for('login_operador'))
        return f(*args, **kwargs)
    return decorated_function

def admin_requerido(f):
    """Filtro de seguridad estricto y exclusivo para Administradores."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        id_usuario = session.get('usuario_id')
        rol_usuario = str(session.get('rol', '')).lower().strip()
        
        if not id_usuario:
            print("🛑 ACCESO DENEGADO: No hay sesión activa.")
            return redirect(url_for('vista_login_admin'))
        
        if 'administrador' not in rol_usuario and 'admin' not in rol_usuario:
            print(f"🛑 ACCESO DENEGADO: El rol '{rol_usuario}' no es Administrador.")
            return redirect(url_for('vista_login_admin'))
            
        return f(*args, **kwargs)
    return decorated_function


# =========================================================================
# 2. ENRUTADORES DE LOGINS Y ACCESO GENERAL
# =========================================================================

@app.route('/')
def inicio():
    return render_template('index.html')

@app.route('/login_operador')
def login_operador():
    return render_template('login.html')

@app.route('/login_admin')
def vista_login_admin():
    return render_template('login_admin.html')

@app.route('/recuperar', methods=['GET'])
def vista_recuperar():
    return render_template('recuperar.html')

@app.route('/cambiar_password', methods=['GET'])
def vista_cambiar_password():
    return render_template('cambio_password.html')


# =========================================================================
# 3. INTERFACES DEL OPERADOR (BLINDADAS)
# =========================================================================

@app.route('/panel_operador')
@login_requerido
def panel_operador():
    return render_template('panel_operador.html')

@app.route('/operador_monitor')
@login_requerido
def operador_monitor():
    # Eliminado el código muerto/duplicado de redirección transparente interna
    return redirect(url_for('panel_operador'))

@app.route('/nuevo_vehiculo')
@login_requerido
def nuevo_vehiculo():
    # Seguridad unificada mediante el decorador superior
    return render_template('nuevo_vehiculo.html')

@app.route('/inventario_operador')
@login_requerido
def inventario_operador():
    return render_template('inventario_operador.html')

@app.route('/bitacora_dias')
@login_requerido
def bitacora_dias():
    return render_template('bitacora_dias.html')

@app.route('/lista_negra')
@login_requerido  # CORREGIDO: Estaba completamente pública, ahora protegida
def vista_lista_negra():
    return render_template('lista_negra.html')

@app.route('/panel_observaciones')
@login_requerido  # CORREGIDO: Estaba desprotegida
def panel_observaciones():
    return render_template('panel_observaciones.html')

@app.route('/contingencia')
@login_requerido  
def vista_contingencia():
    return render_template('registro_contingencia.html')
  
@app.route('/operador/incidentes')
@login_requerido  
def vista_incidentes():
    return render_template('operador_incidentes.html')


# =========================================================================
# 4. INTERFACES DEL ADMINISTRADOR (COMPLETAMENTE BLINDADAS)
# =========================================================================

@app.route('/panel_admin')
@admin_requerido
def panel_admin():
    return render_template('panel_admin.html', vista='bienvenida')

@app.route('/admin/usuarios')
@admin_requerido  # CORREGIDO: Cualquiera podía entrar si sabía la URL
def admin_usuarios():
    return render_template('admin_usuarios.html', vista='usuarios')

@app.route('/admin_inventario')
@admin_requerido  # CORREGIDO: Protección añadida
def admin_inventario():
    return render_template('admin_inventario.html')

@app.route('/admin_registro')
@admin_requerido  # CORREGIDO: Protección añadida
def admin_registro():
    return render_template('admin_registro.html')

@app.route('/admin_listanegra', methods=['GET'])
@admin_requerido  # CORREGIDO: Protección añadida
def vista_admin_listanegra():
    return render_template('admin_listanegra.html', vista='admin_listanegra')

@app.route('/admin_productividad', methods=['GET'])
@admin_requerido  # CORREGIDO: Soluciona el error de renderizado previo
def vista_admin_productividad():
    return render_template('admin_productividad.html')

@app.route('/admin_historial_vigilantes', methods=['GET'])
@admin_requerido  # CORREGIDO: Protección añadida
def vista_admin_historial_vigilantes():
    return render_template('admin_historial_vigilantes.html')

@app.route('/admin_dashboard', methods=['GET'])
@admin_requerido  # CORREGIDO: Protección añadida
def vista_admin_dashboard():
    return render_template('admin_dashboard.html')    

@app.route('/admin_zona')
@admin_requerido
def vista_admin_zona():
    return render_template('admin_zona.html', vista='admin_zona')  


# =========================================================================
# 5. APIS DE CONTROL DE SESIONES (SALIDAS UNIFICADAS)
# =========================================================================

@app.route('/logout')
@app.route('/salir')
def logout():
    """Cierre de sesión global. Limpia datos y redirige al inicio seguro."""
    session.clear()
    return redirect(url_for('inicio'))

@app.route('/logout_admin')
def logout_admin():
    """Cierre de sesión específico para administradores."""
    session.clear()
    return redirect(url_for('vista_login_admin'))






# LOGIN: Verifica el usuario en la BD, bloquea las cuentas pendientes o suspendidas, 
# y activa su sesión si todo está en orden

@app.route('/api/login', methods=['POST'])
def login():
    try:
        datos = request.json
        correo = datos.get('correo', '').strip()
        password = datos.get('password', '')

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT id, nombre, correo, rol, estado 
            FROM usuarios 
            WHERE correo = %s AND password = %s
        """
        cursor.execute(sql, (correo, password))
        usuario = cursor.fetchone()

        cursor.close()
        conn.close()

        if usuario:
            # Control estricto de nulos o vacíos en la columna rol
            if not usuario['rol'] or str(usuario['rol']).strip() == "":
                return jsonify({"status": "error", "message": "Su usuario no tiene un rol asignado en el sistema."}), 403

            estado_usuario = str(usuario['estado']).lower().strip()
            rol_usuario = str(usuario['rol']).lower().strip()

            # COMPROBACIÓN EXCLUSIVA: Solo permite ingresar si su rol es exactamente 'operador'
            if rol_usuario != 'operador':
                return jsonify({"status": "error", "message": "Acceso denegado. Este formulario es exclusivo para Operadores."}), 403

            # Validación de estados del ENUM
            if estado_usuario != 'activo':
                if estado_usuario == 'pendiente':
                    return jsonify({"status": "error", "message": "Su cuenta no está activa. Verifique su correo."}), 403
                elif estado_usuario == 'suspendido':
                    return jsonify({"status": "error", "message": "Su cuenta se encuentra suspendida."}), 403

            # Si pasa todas las auditorías, creamos la sesión con sus datos REALES de la BD
            session.clear()
            session['usuario_id'] = usuario['id']
            session['usuario_nombre'] = usuario['nombre']
            session['usuario_rol'] = rol_usuario  # Guarda 'operador' de la BD
            session['rol'] = rol_usuario          # Sincroniza con el filtro de la ruta
            
            session.modified = True

            return jsonify({
                "status": "success",
                "message": "Acceso concedido.",
                "nombre": usuario['nombre'],
                "rol": rol_usuario 
            }), 200
        else:
            return jsonify({"status": "error", "message": "Credenciales incorrectas o usuario inexistente."}), 401

    except Exception as e:
        print(f"❌ ERROR EN LOGIN OPERADOR: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500





#  LOGIN ADMIN: Valida las credenciales del Administrador, 
# verifica que su cuenta esté activa y abre su sesión de control.
@app.route('/api/login_admin', methods=['POST'])
def login_admin():
    try:
        datos = request.json
        correo = datos.get('correo', '').strip()
        password = datos.get('password', '')

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        # --- CORRECCIÓN: Buscamos solo por correo para poder rastrear los intentos ---
        sql = """
            SELECT id, nombre, correo, password, rol, estado, intentos_fallidos 
            FROM usuarios 
            WHERE correo = %s
        """
        cursor.execute(sql, (correo,))
        usuario = cursor.fetchone()

        if not usuario:
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "Credenciales de administrador incorrectas."}), 401

        estado_usuario = str(usuario['estado']).lower().strip()
        rol_usuario = str(usuario['rol']).lower().strip()

        # 1. COMPROBACIÓN DE ROL EXCLUSIVA: Solo administradores
        if rol_usuario != 'administrador':
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "Acceso denegado. Este formulario es exclusivo para Administradores."}), 403

        # 2. VERIFICAR SI YA ESTÁ SUSPENDIDO/BLOQUEADO
        if estado_usuario == 'suspendido':
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "Su cuenta se encuentra bloqueada por superar el límite de 3 intentos fallidos. Contacte con soporte."}), 403

        if estado_usuario != 'activo':
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "Su cuenta administrativa no está activa."}), 403

        # 3. VERIFICAR LA CONTRASEÑA
        if usuario['password'] == password:
            # ¡ÉXITO! Reiniciamos el contador de intentos fallidos a 0 si tenía errores acumulados
            if usuario['intentos_fallidos'] > 0:
                cursor.execute("UPDATE usuarios SET intentos_fallidos = 0 WHERE id = %s", (usuario['id'],))
                conn.commit()

            cursor.close()
            conn.close()

            # Creamos la sesión del Administrador
            session.clear()
            session['usuario_id'] = usuario['id']
            session['usuario_nombre'] = usuario['nombre']
            session['usuario_rol'] = rol_usuario
            session['rol'] = rol_usuario
            session.modified = True

            return jsonify({
                "status": "success",
                "message": "Acceso administrativo concedido.",
                "nombre": usuario['nombre'],
                "rol": rol_usuario
            }), 200

        else:
            # ❌ CONTRASEÑA INCORRECTA: Sumamos 1 intento fallido
            nuevos_intentos = usuario['intentos_fallidos'] + 1
            
            if nuevos_intentos >= 3:
                # Llegó al límite: Bloqueamos la cuenta cambiando el estado a 'suspendido'
                cursor.execute(
                    "UPDATE usuarios SET intentos_fallidos = %s, estado = 'suspendido' WHERE id = %s", 
                    (nuevos_intentos, usuario['id'])
                )
                conn.commit()
                cursor.close()
                conn.close()
                return jsonify({
                    "status": "error", 
                    "message": "⚠️ Ha superado los 3 intentos permitidos. Su cuenta ha sido bloqueada automáticamente por seguridad."
                }), 403
            else:
                # Aún le quedan intentos: Actualizamos el contador en la BD
                cursor.execute(
                    "UPDATE usuarios SET intentos_fallidos = %s WHERE id = %s", 
                    (nuevos_intentos, usuario['id'])
                )
                conn.commit()
                cursor.close()
                conn.close()
                
                intentos_restantes = 3 - nuevos_intentos
                return jsonify({
                    "status": "error", 
                    "message": f"Contraseña incorrecta. Le quedan {intentos_restantes} intento(s) antes de bloquear la cuenta."
                }), 401

    except Exception as e:
        print(f"❌ ERROR EN LOGIN ADMIN: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500





#  Contraseña / Codigos


def enviar_correo_verificacion(destinatario, codigo, es_recuperacion=False):
    try:
        msg = MIMEMultipart()
        msg['From'] = CORREO_SENDER
        msg['To'] = destinatario
        
        # 🔄 Evaluamos si viene del flujo de recuperación o del registro del Administrador
        if es_recuperacion:
            msg['Subject'] = "🔒 Código de Recuperación de Contraseña - EJARAD TIC"
            cuerpo = f"Tu código de recuperación para restablecer la contraseña en el sistema es: {codigo}"
        else:
            # Se queda exactamente igual a como lo tenías para el registro de usuarios nuevos
            msg['Subject'] = "🔢 Código de Verificación Vehicular"
            cuerpo = f"Tu código de activación y seguridad para ingresar al sistema es: {codigo}"
        
        msg.attach(MIMEText(cuerpo, 'plain'))
        
        # Conexión segura SMTP
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(CORREO_SENDER, CORREO_PASSWORD)
        server.sendmail(CORREO_SENDER, destinatario, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"❌ Error SMTP al enviar código (Es recuperación: {es_recuperacion}): {e}")
        return False

@app.route('/api/verificar_codigo', methods=['POST'])
def verificar_codigo():
    datos = request.json
    correo = datos.get('correo')
    codigo_ingresado = datos.get('codigo')
    
    if not correo or not codigo_ingresado: 
        return jsonify({"status": "error", "message": "Campos faltantes."}), 400
        
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # --- CORRECCIÓN AQUÍ: Se añade 'telefono' a la consulta SELECT ---
        cursor.execute("SELECT id, telefono FROM usuarios WHERE correo = %s AND estado = 'pendiente'", (correo,))
        user = cursor.fetchone()
        
        if not user: 
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "Usuario no encontrado o ya activo."}), 404
        
        # Validamos el código de manera estricta (Ahora 'telefono' sí existe en el diccionario)
        if user['telefono'] != str(codigo_ingresado):
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "El código de verificación es incorrecto."}), 400
            
        # Activamos la cuenta cambiando el estado a 'activo'
        cursor.execute("UPDATE usuarios SET estado = 'activo' WHERE correo = %s", (correo,))
        conn.commit()
        
        cursor.close()
        conn.close()
        return jsonify({"status": "success", "message": "¡Cuenta activada con éxito! Ya puede ingresar."}), 200

    except mysql.connector.Error as err: 
        return jsonify({"status": "error", "message": str(err)}), 500
    

@app.route('/api/solicitar_recuperacion', methods=['POST'])
def solicitar_recuperacion():
    datos = request.json
    correo = datos.get('correo', '').strip()
    
    if not correo: 
        return jsonify({"status": "error", "message": "El correo es obligatorio."}), 400
        
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Verificamos si el correo existe en la base de datos
        cursor.execute("SELECT id FROM usuarios WHERE correo = %s", (correo,))
        if not cursor.fetchone(): 
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "El correo ingresado no pertenece a ningún operador."}), 404
            
        # Generamos el código token de 6 dígitos
        codigo_token = str(random.randint(100000, 999999))
        
        # 🔥 AQUÍ PASAMOS 'es_recuperacion=True' para que use el nuevo texto solo en esta ruta
        if not enviar_correo_verificacion(correo, codigo_token, es_recuperacion=True): 
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "Error en el servidor SMTP al enviar el correo."}), 500
            
        # Guardamos temporalmente el código en la columna telefono
        cursor.execute("UPDATE usuarios SET telefono = %s WHERE correo = %s", (codigo_token, correo))
        conn.commit()
        
        cursor.close()
        conn.close()
        return jsonify({"status": "success", "message": "Código de recuperación enviado con éxito a tu bandeja."}), 200
        
    except mysql.connector.Error as err: 
        print(f"❌ ERROR EN ENVIAR RECUPERACIÓN: {err}")
        return jsonify({"status": "error", "message": str(err)}), 500

@app.route('/api/validar_codigo_recuperacion', methods=['POST'])
def validar_codigo_recuperacion():
    datos = request.json
    correo = datos.get('correo', '').strip()
    codigo_ingresado = datos.get('codigo', '').strip()
    
    if not correo or not codigo_ingresado: 
        return jsonify({"status": "error", "message": "Datos incompletos."}), 400
        
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Buscamos el valor guardado en 'telefono'
        cursor.execute("SELECT telefono FROM usuarios WHERE correo = %s", (correo,))
        user = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        # Validación estricta del código alfanumérico/numérico
        if user and user['telefono'] == str(codigo_ingresado):
            return jsonify({"status": "success"}), 200
        else:
            return jsonify({"status": "error", "message": "El código de recuperación es incorrecto."}), 400
            
    except mysql.connector.Error as err: 
        print(f"❌ ERROR EN VALIDAR CÓDIGO: {err}")
        return jsonify({"status": "error", "message": str(err)}), 500


@app.route('/api/actualizar_password', methods=['POST'])
def actualizar_password():
    datos = request.json
    correo = datos.get('correo', '').strip()
    nueva_password = datos.get('password', '').strip()
    
    if not correo or not nueva_password: 
        return jsonify({"status": "error", "message": "Campos obligatorios."}), 400
        
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Obtenemos la contraseña actual para validar que no sea idéntica
        cursor.execute("SELECT password FROM usuarios WHERE correo = %s", (correo,))
        user = cursor.fetchone()
        
        if not user: 
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "Usuario no encontrado."}), 404
            
        if user['password'] == nueva_password: 
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "La nueva contraseña no puede ser idéntica a la anterior."}), 400
            
        # --- CORRECCIÓN: Actualizamos la contraseña SIN poner en NULL el teléfono para no destruir datos ---
        cursor.execute("UPDATE usuarios SET password = %s WHERE correo = %s", (nueva_password, correo))
        conn.commit()
        
        cursor.close()
        conn.close()
        return jsonify({"status": "success", "message": "Contraseña restablecida con éxito. Ya puede iniciar sesión."}), 200
        
    except mysql.connector.Error as err: 
        print(f"❌ ERROR EN ACTUALIZAR PASSWORD: {err}")
        return jsonify({"status": "error", "message": str(err)}), 500
  #  Contraseña   



#  Panel admin
#  Panel admin
# GESTION DE USUSARIOS
@app.route('/api/admin_registrar_personal', methods=['POST'])
def admin_registrar_personal():
    try:
        datos = request.json
        nombre = datos.get('nombre')
        dni = str(datos.get('dni', '')).strip()
        telefono_real = str(datos.get('telefono', '')).strip() # Lo convertimos a string de forma segura
        correo = str(datos.get('correo', '')).strip()
        password = datos.get('password')
        rol = datos.get('rol')

        if not all([nombre, dni, telefono_real, correo, password, rol]):
            return jsonify({"status": "error", "message": "Todos los campos son obligatorios."}), 400

        # Validar que el DNI tenga exactamente 8 números
        if len(dni) != 8 or not dni.isdigit():
            return jsonify({"status": "error", "message": "El DNI debe contener exactamente 8 dígitos."}), 400

        # --- NUEVA CORRECCIÓN: Validar que el teléfono tenga exactamente 9 números ---
        if len(telefono_real) != 9 or not telefono_real.isdigit():
            return jsonify({"status": "error", "message": "El teléfono debe contener exactamente 9 dígitos numéricos."}), 400

        if "@" not in correo:
            return jsonify({"status": "error", "message": "El correo electrónico debe incluir el símbolo '@'."}), 400

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM usuarios WHERE correo = %s", (correo,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": "El correo ya se encuentra registrado."}), 400

        # 🔢 GENERAMOS EL CÓDIGO DE 6 DÍGITOS
        codigo_verificacion = str(random.randint(100000, 999999))

        # 🚨 IMPORTANTE: Guardamos el código en 'telefono' como lo requiere tu ruta de verificación
        # (Nota: Recuerda que cuando valide el código, podrás sobreescribir este campo con 'telefono_real')
        sql = """
            INSERT INTO usuarios (nombre, dni, telefono, correo, password, rol, estado)
            VALUES (%s, %s, %s, %s, %s, %s, 'pendiente')
        """
        cursor.execute(sql, (nombre, dni, codigo_verificacion, correo, password, rol))
        conn.commit()
        cursor.close()
        conn.close()

        # ✉️ ENVIAMOS EL CORREO ELECTRÓNICO REAL EN SEGUNDO PLANO
        import threading
        threading.Thread(target=enviar_correo_verificacion, args=(correo, codigo_verificacion)).start()

        return jsonify({
            "status": "success", 
            "message": f"Personal '{nombre}' registrado. Código de activación enviado a {correo}."
        }), 200

    except Exception as e:
        print(f"❌ ERROR EN REGISTRO DE PERSONAL: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    
@app.route('/api/actualizar_correo_operador', methods=['POST'])
def actualizar_correo_operador():
    try:
        datos = request.get_json()
        usuario_id = datos.get('id')
        nuevo_correo = str(datos.get('correo', '')).strip()

        if not usuario_id or not nuevo_correo:
            return jsonify({"status": "error", "message": "Datos incompletos."}), 400

        if "@" not in nuevo_correo:
            return jsonify({"status": "error", "message": "El correo electrónico debe incluir el símbolo '@'."}), 400

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("UPDATE usuarios SET correo = %s WHERE id = %s", (nuevo_correo, usuario_id))
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"status": "success", "message": "Correo actualizado correctamente."})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/reenviar_codigo', methods=['POST'])
def reenviar_codigo():
    try:
        datos = request.get_json()
        usuario_id = datos.get('id')
        correo = str(datos.get('correo', '')).strip()

        if not usuario_id or not correo:
            return jsonify({"status": "error", "message": "Datos incompletos."}), 400

        codigo_verificacion = str(random.randint(100000, 999999))

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("UPDATE usuarios SET telefono = %s, intentos_fallidos = 0 WHERE id = %s", (codigo_verificacion, usuario_id))
        conn.commit()
        cursor.close()
        conn.close()

        threading.Thread(target=enviar_correo_verificacion, args=(correo, codigo_verificacion)).start()

        return jsonify({"status": "success", "message": f"Se ha enviado un nuevo código de activación a: {correo}"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/obtener_operadores', methods=['GET'])
def obtener_operadores():
    try:
        # Conexión nativa usando tu DB_CONFIG global
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # --- CORRECCIÓN: Seleccionamos el personal directamente de 'usuarios' ---
        # Mostramos su ID, Nombre, Correo, el Rol asignado y su Estado de cuenta
        cursor.execute("""
            SELECT id, nombre, correo, rol, estado 
            FROM usuarios 
            ORDER BY nombre ASC
        """)
        
        usuarios_sistema = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(usuarios_sistema), 200
    except Exception as e:
        print(f"❌ ERROR AL OBTENER OPERADORES/PERSONAL: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# CAMBIAR ESTADO DE CUENTA (ENUM)
@app.route('/api/cambiar_estado_operador', methods=['POST'])
def cambiar_estado_operador():
    try:
        datos = request.json
        id_usuario = datos.get('id')
        nuevo_estado = datos.get('estado') # 'pendiente', 'activo', 'suspendido'
        
        if not id_usuario or not nuevo_estado: 
            return jsonify({"status": "error", "message": "Datos incompletos."}), 400
            
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        cursor.execute("UPDATE usuarios SET estado = %s WHERE id = %s", (nuevo_estado, id_usuario))
        conn.commit()
        
        cursor.close()
        conn.close()
        return jsonify({"status": "success", "message": f"Estado cambiado a '{nuevo_estado}'."}), 200
    except Exception as e: 
        print(f"❌ ERROR AL CAMBIAR ESTADO: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# LISTAR TODOS LOS USUARIOS DEL SISTEMA

@app.route('/api/obtener_usuarios', methods=['GET'])
def obtener_usuarios():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, nombre, correo, rol, estado FROM usuarios ORDER BY id DESC")
        usuarios = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(usuarios), 200
    except Exception as e:
        print(f"❌ ERROR AL OBTENER USUARIOS: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500



# AFORO DE LA ZONA
# 1. API: OBTIENE MÉTRICAS Y LA CAPACIDAD DINÁMICA
@app.route('/api/obtener_metricas_admin')
def obtener_metricas_admin():
    if not session.get('usuario_id'):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True) 

        # --- 1. CAPACIDAD DE LA ZONA (tabla: zonas_parqueo) ---
        cursor.execute("SELECT capacidad_zona FROM zonas_parqueo WHERE id = 1")
        resultado_zona = cursor.fetchone()
        capacidad_zona = resultado_zona['capacidad_zona'] if resultado_zona else 20

        # --- 2. VEHÍCULOS INTERNOS (tabla: historial_accesos) ---
        # Sigue adentro si registró ingreso pero su fecha_salida está vacía (NULL)
        cursor.execute("SELECT COUNT(*) as total FROM historial_accesos WHERE fecha_salida IS NULL")
        vehiculos_dentro = cursor.fetchone()['total']

        # --- 3. FLOTA VEHICULAR ACTIVA (tabla: vehiculos) ---
        cursor.execute("SELECT COUNT(*) as total FROM vehiculos WHERE id_estado = 1")
        total_vehiculos = cursor.fetchone()['total']

        # --- 4. LISTA NEGRA (tabla: lista_negra) ---
        cursor.execute("SELECT COUNT(*) as total FROM lista_negra") 
        total_lista_negra = cursor.fetchone()['total']

        # --- 5. VIGILANTES ACTIVOS (tabla: usuarios) ---
        # Filtra por tu columna rol (asumiendo 'vigilante' o 'operador') y estado 'activo'
        cursor.execute("SELECT COUNT(*) as total FROM usuarios WHERE rol = 'vigilante' AND estado = 'activo'")
        usuarios_activos = cursor.fetchone()['total']

        # --- CALCULAR DISPONIBILIDAD DINÁMICA ---
        disponibilidad_cochera = capacidad_zona - vehiculos_dentro
        if disponibilidad_cochera < 0: 
            disponibilidad_cochera = 0

        cursor.close()

        return jsonify({
            'status': 'success',
            'vehiculos_dentro': vehiculos_dentro,
            'total_vehiculos': total_vehiculos,
            'total_lista_negra': total_lista_negra,
            'usuarios_activos': usuarios_activos,
            'capacidad_zona': capacidad_zona,
            'disponibilidad_cochera': disponibilidad_cochera
        })

    except Exception as e:
        print(f"Error crítico en la API de métricas: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn and conn.is_connected():
            conn.close()


# 2. API: ACTUALIZA LA CAPACIDAD EN MYSQL
@app.route('/api/actualizar_capacidad', methods=['POST'])
def actualizar_capacidad():
    if not session.get('usuario_id'):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    conn = None
    try:
        datos = request.get_json()
        nueva_capacidad = int(datos.get('capacidad', 0))

        if nueva_capacidad <= 0:
            return jsonify({'status': 'error', 'message': 'La capacidad debe ser mayor que 0'}), 400

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # Actualiza tu tabla zonas_parqueo usando la columna capacidad_zona
        cursor.execute("UPDATE zonas_parqueo SET capacidad_zona = %s WHERE id = 1", (nueva_capacidad,))
        conn.commit() 
        cursor.close()

        return jsonify({'status': 'success', 'message': 'Capacidad guardada'})

    except Exception as e:
        print(f"Error al actualizar capacidad: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn and conn.is_connected():
            conn.close()



# CONTROL DE VEHICULOS
@app.route('/api/obtener_todos_vehiculos_admin', methods=['GET'])
def obtener_todos_vehiculos_admin():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        sql = """
            SELECT v.placa, v.marca, v.modelo, v.color, v.tipo_vehiculo, v.id_estado, 
                   p.nombre_agencia AS propietario, p.telefono_contacto AS telefono
            FROM vehiculos v
            LEFT JOIN propietarios_vehiculos p ON v.id_propietario = p.id
            ORDER BY v.fecha_registro DESC
        """
        cursor.execute(sql)
        vehiculos = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return jsonify({"status": "success", "data": vehiculos}), 200
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/admin_registrar_vehiculo', methods=['POST'])
def admin_registrar_vehiculo():
    try:
        datos = request.json
        placa = datos.get('placa', '').strip().upper()
        marca = datos.get('marca', '').strip()
        modelo = datos.get('modelo', '').strip()
        color = datos.get('color', '').strip()
        tipo_vehiculo = datos.get('tipo_vehiculo', '').strip()
        nombre_agencia = datos.get('nombre_agencia', '').strip()

        if not placa or not nombre_agencia:
            return jsonify({"status": "error", "message": "Placa y Propietario/Agencia son obligatorios."}), 400

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("SELECT placa FROM vehiculos WHERE placa = %s", (placa,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": f"El vehículo con placa '{placa}' ya existe."}), 400

        cursor.execute("SELECT id FROM propietarios_vehiculos WHERE nombre_agencia = %s OR nombre_completo = %s", (nombre_agencia, nombre_agencia))
        agencia_res = cursor.fetchone()
        
        if agencia_res:
            id_propietario = agencia_res[0]
        else:
            cursor.execute("""
                INSERT INTO propietarios_vehiculos (nombre_completo, nombre_agencia) 
                VALUES (%s, %s)
            """, (nombre_agencia, nombre_agencia))
            id_propietario = cursor.lastrowid

        sql = """
            INSERT INTO vehiculos (placa, id_propietario, marca, modelo, color, tipo_vehiculo, id_estado)
            VALUES (%s, %s, %s, %s, %s, %s, 1)
        """
        cursor.execute(sql, (placa, id_propietario, marca, modelo, color, tipo_vehiculo))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({"status": "success", "message": "Vehículo registrado correctamente en el inventario."}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/editar_vehiculo', methods=['POST'])
def editar_vehiculo():
    datos = request.json
    placa_original = datos.get('placa_original', '').strip().upper()
    nueva_placa = datos.get('placa', '').strip().upper()
    marca = datos.get('marca', '').strip()
    modelo = datos.get('modelo', '').strip()
    color = datos.get('color', '').strip()
    tipo_vehiculo = datos.get('tipo_vehiculo', '').strip()
    id_estado = datos.get('id_estado')
    
    if not placa_original or not nueva_placa:
        return jsonify({"status": "error", "message": "La placa es un campo obligatorio."}), 400

    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT placa FROM vehiculos WHERE placa = %s", (placa_original,))
        if not cursor.fetchone():
            return jsonify({"status": "error", "message": "El vehículo no existe."}), 404

        if placa_original != nueva_placa:
            cursor.execute("SELECT placa FROM vehiculos WHERE placa = %s", (nueva_placa,))
            if cursor.fetchone():
                return jsonify({"status": "error", "message": f"La placa '{nueva_placa}' ya está registrada."}), 400

        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        
        cursor.execute("UPDATE lista_negra SET placa = %s WHERE placa = %s", (nueva_placa, placa_original))
        cursor.execute("UPDATE historial_accesos SET placa = %s WHERE placa = %s", (nueva_placa, placa_original))

        query_update = """
            UPDATE vehiculos 
            SET placa = %s, marca = %s, modelo = %s, color = %s, tipo_vehiculo = %s, id_estado = %s
            WHERE placa = %s
        """
        cursor.execute(query_update, (nueva_placa, marca, modelo, color, tipo_vehiculo, id_estado, placa_original))
        
        conn.commit()
        return jsonify({"status": "success", "message": "Vehículo actualizado correctamente."}), 200

    except mysql.connector.Error as err:
        return jsonify({"status": "error", "message": str(err)}), 500

    finally:
        if conn and cursor:
            try:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
            except:
                pass
            cursor.close()
            conn.close()

@app.route('/api/cambiar_estado_vehiculo', methods=['POST'])
def cambiar_estado_vehiculo():
    try:
        datos = request.get_json()
        placa = datos.get('placa')
        nuevo_estado = datos.get('id_estado')

        if not placa or nuevo_estado is None:
            return jsonify({"status": "error", "message": "Datos incompletos."}), 400

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("UPDATE vehiculos SET id_estado = %s WHERE placa = %s", (nuevo_estado, placa))
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"status": "success", "message": "Estado del vehículo actualizado."})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
@app.route('/api/registrar_vehiculo', methods=['POST'])
def registrar_vehiculo():
    data = request.get_json()
    if not data:
        return jsonify({"message": "Error: No se recibieron datos en la petición."}), 400

    placa = data.get('placa')
    if not placa or placa.strip() == "":
        return jsonify({"message": "Error: La placa es un campo obligatorio."}), 400

    # 1. Campos de la tabla: vehiculos (¡Aquí es donde va id_estado ahora!)
    marca = data.get('marca', 'S/M')
    modelo = data.get('modelo', 'S/M')
    color = data.get('color', 'S/C')
    tipo_vehiculo = data.get('tipo_vehiculo', 'S/T')
    
    try:
        id_estado = int(data.get('id_estado', 1)) # Estado por defecto (1)
    except:
        id_estado = 1

    # 2. Campos de la tabla: propietarios_vehiculos
    nombre_agencia = data.get('agencia_propietaria') 
    telefono_contacto = data.get('telefono_agencia', '---')

    # 3. Campos de la tabla: conductores (Sin id_estado)
    dni_conductor = data.get('dni_conductor')
    nombre_conductor = data.get('nombre_conductor')
    telefono_conductor = data.get('telefono_conductor', '---')

    conexion = None
    cursor = None

    try:
        conexion = mysql.connector.connect(**DB_CONFIG)
        cursor = conexion.cursor()

        # ---------------------------------------------------------------------
        # PASO A: Inserción en 'propietarios_vehiculos'
        # Columnas: nombre_agencia, telefono_contacto
        # ---------------------------------------------------------------------
        sql_propietario = """INSERT INTO propietarios_vehiculos (nombre_agencia, telefono_contacto) 
                             VALUES (%s, %s)"""
        cursor.execute(sql_propietario, (nombre_agencia, telefono_contacto))
        id_propietario = cursor.lastrowid # Recuperamos el id generado

        # ---------------------------------------------------------------------
        # PASO B: Inserción en 'conductores'
        # Columnas: id, dni_conductor, nombre_conductor, telefono_conductor
        # ---------------------------------------------------------------------
        sql_conductor = """INSERT INTO conductores (dni_conductor, nombre_conductor, telefono_conductor) 
                           VALUES (%s, %s, %s)"""
        cursor.execute(sql_conductor, (dni_conductor, nombre_conductor, telefono_conductor))

        # ---------------------------------------------------------------------
        # PASO C: Inserción en 'vehiculos'
        # Columnas: placa, id_propietario, marca, modelo, color, tipo_vehiculo, id_estado
        # (Nota: fecha_registro se genera automático en MySQL por ser TIMESTAMP)
        # ---------------------------------------------------------------------
        sql_vehiculo = """INSERT INTO vehiculos (placa, id_propietario, marca, modelo, color, tipo_vehiculo, id_estado) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s)"""
        cursor.execute(sql_vehiculo, (placa, id_propietario, marca, modelo, color, tipo_vehiculo, id_estado))

        # ---------------------------------------------------------------------
        # 🔥 CONFIRMACIÓN DE LA TRANSACCIÓN COMPLETA
        # ---------------------------------------------------------------------
        conexion.commit()

        print(f"🎉 ¡Éxito absoluto! Unidad {placa} registrada correctamente.")
        return jsonify({"message": "✔️ Vehículo registrado exitosamente en el inventario relacional."}), 200

    except mysql.connector.Error as err:
        if conexion:
            conexion.rollback() # Revierte todo si algo falla para no dejar datos corruptos
        print(f"❌ Error de MySQL en inserción: {err}")
        return jsonify({"message": f"Error en la base de datos: {err.msg}"}), 500

    except Exception as e:
        if conexion:
            conexion.rollback()
        print(f"❌ Error inesperado en Python: {str(e)}")
        return jsonify({"message": f"Error inesperado: {str(e)}"}), 500

    finally:
        if cursor:
            cursor.close()
        if conexion and conexion.is_connected():
            conexion.close()


# LISTA NEGRA
@app.route('/api/registrar_lista_negra', methods=['POST'])
def registrar_lista_negra():
    try:
        datos = request.json
        if not datos:
            return jsonify({"status": "error", "message": "No se recibieron datos."}), 400

        placa_input = datos.get('placa', '').strip().upper()
        motivo = datos.get('motivo_bloqueo', '').strip().upper()

        if not placa_input or not motivo:
            return jsonify({"status": "error", "message": "Todos los campos son obligatorios."}), 400

        ID_ESTADO_DESACTIVADO = 2 

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        # Quitamos espacios y guiones para hacer una búsqueda limpia e inteligente
        placa_limpia = placa_input.replace("-", "").replace(" ", "")

        # Verificar si el vehículo existe en el inventario general
        sql_verificar_vehiculo = "SELECT placa FROM vehiculos WHERE REPLACE(placa, '-', '') = %s"
        cursor.execute(sql_verificar_vehiculo, (placa_limpia,))
        vehiculo_existe = cursor.fetchone()

        if not vehiculo_existe:
            cursor.close()
            conn.close()
            return jsonify({
                "status": "error", 
                "message": f"El vehículo con placa {placa_input} no se encuentra registrado en el inventario general."
            }), 404

        # Tomamos la placa exacta tal cual está escrita en tu tabla 'vehiculos'
        placa_real_bd = vehiculo_existe['placa']

        # Verificar duplicados en la lista negra
        sql_verificar_ln = "SELECT placa FROM lista_negra WHERE REPLACE(placa, '-', '') = %s"
        cursor.execute(sql_verificar_ln, (placa_limpia,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({"status": "error", "message": f"El vehículo {placa_input} ya se encuentra en la Lista Negra."}), 400

        # ⚡ OPERACIONES DIRECTAS CON COMMIT INDEPENDIENTE
        # Operación A: Insertar el bloqueo usando el nombre correcto de columna 'motivo_bloqueo'
        cursor.execute("INSERT INTO lista_negra (placa, motivo_bloqueo) VALUES (%s, %s)", (placa_real_bd, motivo))
        conn.commit()

        # Operación B: Cambiar el estado del vehículo a Desactivado (2)
        sql_update = "UPDATE vehiculos SET id_estado = %s WHERE REPLACE(placa, '-', '') = %s"
        cursor.execute(sql_update, (ID_ESTADO_DESACTIVADO, placa_limpia))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "status": "success", 
            "message": f"✔️ Vehículo {placa_real_bd} restringido en la Lista Negra y estado cambiado a Desactivado con éxito."
        }), 200
        
    except Exception as e:
        print(f"❌ Error en registro de Lista Negra: {str(e)}")
        return jsonify({"status": "error", "message": f"Error interno: {str(e)}"}), 500


@app.route('/api/obtener_lista_negra', methods=['GET'])
def obtener_lista_negra():
    if not session.get('usuario_id'):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 🔥 CORREGIDO: Se seleccionan las columnas reales de tu tabla 'lista_negra'
        sql = """
            SELECT id, placa, motivo_bloqueo, 
                   DATE_FORMAT(fecha_bloqueo, '%d/%m/%Y %H:%i') as fecha_bloqueo 
            FROM lista_negra 
            ORDER BY id DESC
        """
        cursor.execute(sql)
        vehiculos = cursor.fetchall()
        cursor.close()

        return jsonify({
            'status': 'success',
            'vehiculos': vehiculos
        })
    except Exception as e:
        print(f"❌ Error al obtener lista negra: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn and conn.is_connected():
            conn.close()

@app.route('/api/quitar_lista_negra/<int:id_registro>', methods=['DELETE'])
def quitar_lista_negra(id_registro):
    if not session.get('usuario_id'):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    try:
        ID_ESTADO_ACTIVO = 1
        
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        # Obtener la placa antes de borrar la restricción
        cursor.execute("SELECT placa FROM lista_negra WHERE id = %s", (id_registro,))
        resultado = cursor.fetchone()
        
        if not resultado:
            cursor.close()
            conn.close()
            return jsonify({'status': 'error', 'message': 'El registro de restricción no existe.'}), 404
            
        placa_bloqueada = resultado['placa']
        placa_limpia = placa_bloqueada.replace("-", "").replace(" ", "")

        # ⚡ ACCIONES EN CADENA
        # Acción A: Restaurar el estado en la tabla vehiculos vinculando sin guiones
        sql_restaurar = "UPDATE vehiculos SET id_estado = %s WHERE REPLACE(placa, '-', '') = %s"
        cursor.execute(sql_restaurar, (ID_ESTADO_ACTIVO, placa_limpia))
        conn.commit()
        
        # Acción B: Eliminar físicamente el registro de la lista negra
        cursor.execute("DELETE FROM lista_negra WHERE id = %s", (id_registro,))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success', 
            'message': f'Vehículo {placa_bloqueada} removido de la Lista Negra y restaurado con éxito.'
        })

    except Exception as e:
        print(f"❌ Error al remover de la lista negra: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500



# REPORTE PRODUCTIVDAD
@app.route('/api/reporte_productividad', methods=['GET'])
def reporte_productividad():
    try:
        # 1. Recuperar los filtros de la URL
        fecha_inicio = request.args.get('fecha_inicio', '').strip()
        fecha_fin = request.args.get('fecha_fin', '').strip()
        turno = request.args.get('turno', 'todos').strip()

        if not fecha_inicio or not fecha_fin:
            return jsonify({"status": "error", "message": "Filtros de fechas incompletos."}), 400

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True) # Devuelve los datos limpios como diccionarios

        # 2. Configurar la restricción horaria por turnos usando 'fecha_ingreso'
        restriccion_turno_sql = ""
        if turno == "madrugada":
            restriccion_turno_sql = "AND TIME(h.fecha_ingreso) BETWEEN '00:00:00' AND '05:59:59'"
        elif turno == "manana":
            restriccion_turno_sql = "AND TIME(h.fecha_ingreso) BETWEEN '06:00:00' AND '11:59:59'"
        elif turno == "tarde":
            restriccion_turno_sql = "AND TIME(h.fecha_ingreso) BETWEEN '12:00:00' AND '17:59:59'"
        elif turno == "noche":
            restriccion_turno_sql = "AND TIME(h.fecha_ingreso) BETWEEN '18:00:00' AND '23:59:59'"

        # 3. Consulta SQL Analítica adaptada a tus columnas reales
        # Cuenta cuántas placas atendió cada id_operador (mapeado con usuarios)
        sql = f"""
            SELECT 
                h.id_operador AS id,
                u.nombre AS nombre,
                u.rol AS rol,
                COUNT(h.id) AS total_atendidos
            FROM historial_accesos h
            INNER JOIN usuarios u ON h.id_operador = u.id
            WHERE DATE(h.fecha_ingreso) BETWEEN %s AND %s
            {restriccion_turno_sql}
            GROUP BY h.id_operador, u.nombre, u.rol
            ORDER BY total_atendidos DESC
        """

        cursor.execute(sql, (fecha_inicio, fecha_fin))
        resultados = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            "status": "success",
            "data": resultados
        }), 200

    except Exception as e:
        if 'conn' in locals() and conn.is_connected():
            conn.close()
        print(f"❌ Error en Reporte de Productividad: {str(e)}")
        return jsonify({"status": "error", "message": f"Error interno del servidor: {str(e)}"}), 500


# Historial de los vigilantes
@app.route('/api/auditoria_detallada_operador', methods=['GET'])
def auditoria_detallada_operador():
    try:
        operador_busqueda = request.args.get('operador', '').strip()
        fecha_inicio = request.args.get('fecha_inicio')
        fecha_fin = request.args.get('fecha_fin')
        turno = request.args.get('turno', 'todos')

        if not operador_busqueda or not fecha_inicio or not fecha_fin:
            return jsonify({"status": "error", "message": "Debe ingresar el operador y el rango de fechas."}), 400

        # Consulta estructurada con tus nombres reales de columnas: p.nombre_agencia e h.fecha_ingreso
        sql = """
            SELECT h.placa, h.fecha_ingreso, h.fecha_salida, 
                   p.nombre_agencia AS propietario, u.nombre AS nombre_operador
            FROM historial_accesos h
            INNER JOIN usuarios u ON h.id_operador = u.id
            INNER JOIN vehiculos v ON h.placa = v.placa
            INNER JOIN propietarios_vehiculos p ON v.id_propietario = p.id
            WHERE DATE(h.fecha_ingreso) BETWEEN %s AND %s
        """
        parametros = [fecha_inicio, fecha_fin]

        # Filtro inteligente por ID numérico o por Nombre del Vigilante
        if operador_busqueda.isdigit():
            sql += " AND u.id = %s"
            parametros.append(int(operador_busqueda))
        else:
            sql += " AND u.nombre LIKE %s"
            parametros.append(f"%{operador_busqueda}%")

        # Filtro de los 4 turnos laborales sobre el campo h.fecha_ingreso
        if turno == 'madrugada':
            sql += " AND TIME(h.fecha_ingreso) >= '00:00:00' AND TIME(h.fecha_ingreso) < '06:00:00'"
        elif turno == 'manana':
            sql += " AND TIME(h.fecha_ingreso) >= '06:00:00' AND TIME(h.fecha_ingreso) < '12:00:00'"
        elif turno == 'tarde':
            sql += " AND TIME(h.fecha_ingreso) >= '12:00:00' AND TIME(h.fecha_ingreso) < '18:00:00'"
        elif turno == 'noche':
            sql += " AND TIME(h.fecha_ingreso) >= '18:00:00' AND TIME(h.fecha_ingreso) <= '23:59:59'"

        # Requerimiento: Ordenar cronológicamente (Más reciente primero)
        sql += " ORDER BY h.fecha_ingreso DESC"

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, tuple(parametros))
        resultados = cursor.fetchall()
        
        # Formatear fechas para que viajen limpias en el JSON
        for r in resultados:
            if r['fecha_ingreso']:
                r['fecha_ingreso'] = r['fecha_ingreso'].strftime('%Y-%m-%d %H:%M:%S')
            if r['fecha_salida']:
                r['fecha_salida'] = r['fecha_salida'].strftime('%Y-%m-%d %H:%M:%S')
            else:
                r['fecha_salida'] = '---'

        cursor.close()
        conn.close()

        return jsonify({"status": "success", "data": resultados}), 200

    except Exception as e:
        print(f"❌ ERROR EN HISTORIAL VIGILANTES: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# DASBOARD
@app.route('/api/grafico_flujo_vehicular', methods=['GET'])
def grafico_flujo_vehicular():
    try:
        filtro = request.args.get('tipo', 'diario') # 'diario' o 'mensual'
        
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        if filtro == 'diario':
            # 📅 Unificamos en una sola consulta estructurada por fecha real para mantener el orden cronológico estricto
            sql = """
                SELECT 
                    DATE_FORMAT(fecha_ingreso, '%d/%m') AS fecha,
                    COUNT(fecha_ingreso) AS total_entradas,
                    SUM(CASE WHEN fecha_salida IS NOT NULL THEN 1 ELSE 0 END) AS total_salidas,
                    DATE(fecha_ingreso) AS fecha_real
                FROM historial_accesos
                WHERE fecha_ingreso >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                GROUP BY DATE(fecha_ingreso)
                ORDER BY fecha_real ASC
            """
            cursor.execute(sql)
            resultados = cursor.fetchall()

            fechas_set = [row['fecha'] for row in resultados]
            data_entradas = [int(row['total_entradas']) for row in resultados]
            data_salidas = [int(row['total_salidas']) for row in resultados]

        else:
            # 🗓️ Consulta Mensual ordenada por el número real del mes (1 al 12) para que no se altere alfabéticamente
            sql = """
                SELECT 
                    MONTH(fecha_ingreso) AS num_mes,
                    COUNT(fecha_ingreso) AS total_entradas,
                    SUM(CASE WHEN fecha_salida IS NOT NULL THEN 1 ELSE 0 END) AS total_salidas
                FROM historial_accesos
                WHERE YEAR(fecha_ingreso) = YEAR(NOW())
                GROUP BY MONTH(fecha_ingreso)
                ORDER BY num_mes ASC
            """
            cursor.execute(sql)
            resultados = cursor.fetchall()
            
            # Mapeo manual seguro para los meses en texto respetando el orden numérico de la consulta
            meses_nombres = {1: 'Ene', 2: 'Feb', 3: 'Mar', 4: 'Abr', 5: 'May', 6: 'Jun', 
                             7: 'Jul', 8: 'Ago', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dic'}
            
            fechas_set = [meses_nombres.get(row['num_mes'], str(row['num_mes'])) for row in resultados]
            data_entradas = [int(row['total_entradas']) for row in resultados]
            data_salidas = [int(row['total_salidas']) for row in resultados]

        cursor.close()
        conn.close()

        return jsonify({
            "status": "success",
            "fechas": fechas_set,
            "entradas": data_entradas,
            "salidas": data_salidas
        }), 200

    except Exception as e:
        print(f"❌ ERROR EN GRÁFICO: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500 


@app.route('/api/grafico_tipos_vehiculos', methods=['GET'])
def grafico_tipos_vehiculos():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        # 🚗 Controlamos cadenas vacías o espacios en blanco para que Chart.js dibuje etiquetas limpias
        sql = """
            SELECT 
                IFNULL(NULLIF(TRIM(v.tipo_vehiculo), ''), 'No Clasificado') AS tipo, 
                COUNT(h.id) AS total
            FROM historial_accesos h
            INNER JOIN vehiculos v ON h.placa = v.placa
            GROUP BY v.tipo_vehiculo
            ORDER BY total DESC
        """
        cursor.execute(sql)
        resultados = cursor.fetchall()

        cursor.close()
        conn.close()

        tipos = [row['tipo'] for row in resultados]
        totales = [int(row['total']) for row in resultados]

        return jsonify({
            "status": "success",
            "tipos": tipos,
            "totales": totales
        }), 200

    except Exception as e:
        print(f"❌ ERROR EN GRÁFICO DE PASTEL: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    

# PDFs
@app.route('/api/exportar_dashboard_pdf', methods=['GET'])
def exportar_dashboard_pdf():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 1. Obtener métricas actuales para el reporte de forma segura
        cursor.execute("SELECT COUNT(*) AS total FROM historial_accesos")
        total_accesos = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) AS total FROM vehiculos WHERE id_estado = 1")
        vehiculos_activos = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) AS total FROM lista_negra")
        lista_negra = cursor.fetchone()['total']
        
        cursor.close()
        conn.close()
        
        # 2. Configurar buffer y documento PDF en memoria
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        story = []
        
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=20, leading=24, textColor=colors.HexColor('#0f172a'), alignment=1)
        meta_style = ParagraphStyle('Meta', parent=styles['Normal'], fontSize=10, textColor=colors.gray, alignment=2)
        
        # Encabezado
        fecha_actual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        story.append(Paragraph("🛡️ EJARAD TIC - REPORTE EJECUTIVO DE CONTROL", title_style))
        story.append(Spacer(1, 10))
        story.append(Paragraph(f"Fecha de Emisión: {fecha_actual}", meta_style))
        story.append(Spacer(1, 20))
        
        story.append(Paragraph("<b>Resumen Ejecutivo del Sitema</b>", styles['Heading2']))
        story.append(Spacer(1, 10))
        
        # Datos de la Tabla de Métricas
        data = [
            ['Indicador / Módulo', 'Cantidad Actual'],
            ['Total de Accesos Registrados', str(total_accesos)],
            ['Vehículos Habilitados en Sistema', str(vehiculos_activos)],
            ['Vehículos en Lista Negra / Alerta', str(lista_negra)]
        ]
        
        t = Table(data, colWidths=[300, 150])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0f172a')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 12),
            ('BOTTOMPADDING', (0,0), (-1,0), 8),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f8fafc')),
            ('GRID', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f1f5f9')]),
            ('FONTSIZE', (0,1), (-1,-1), 10),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('BOTTOMPADDING', (0,1), (-1,-1), 6),
        ]))
        
        story.append(t)
        doc.build(story)
        
        buffer.seek(0)
        # 🎯 CORRECCIÓN EN FLASK: Se usa download_name y mimetype (sin el guion bajo secundario)
        return send_file(
            buffer, 
            as_attachment=True, 
            download_name=f"REPORTE EJECUTIVO DE CONTROL_{datetime.now().strftime('%Y%m%d')}.pdf", 
            mimetype='application/pdf'
        )
        
    except Exception as e:
        print(f"❌ ERROR EN PDF DASHBOARD: {str(e)}") # Imprime el error exacto en tu consola de comandos
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/exportar_operadores_pdf', methods=['GET'])
def exportar_operadores_pdf():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 🎯 CORRECCIÓN: Quitamos la columna 'usuario' de la consulta SQL para evitar el error 1054
        # Traemos solo los campos estándar que sí existen en tu tabla de usuarios
        sql = "SELECT id, nombre, correo, rol, estado FROM usuarios"
        cursor.execute(sql)
        usuarios = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=30, leftMargin=30, topMargin=40, bottomMargin=40)
        story = []
        
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=18, leading=22, textColor=colors.HexColor('#1e293b'))
        meta_style = ParagraphStyle('Meta', parent=styles['Normal'], fontSize=10, textColor=colors.gray, alignment=2)
        
        fecha_actual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        story.append(Paragraph("👥 Reporte de Personal y Operadores Registrados", title_style))
        story.append(Spacer(1, 10))
        story.append(Paragraph(f"Generado el: {fecha_actual}", meta_style))
        story.append(Spacer(1, 20))
        
        # Diseñar Tabla de Usuarios (Cambiamos la columna 'Nombre de Usuario' por 'Correo Institucional')
        table_data = [['ID', 'Operador', 'Correo', 'Rol', 'Estado']]
        for u in usuarios:
            table_data.append([
                f"#{u['id']}",
                str(u.get('nombre', '---')).title(),
                str(u.get('correo', '---')),
                str(u.get('rol', '---')).upper(),
                str(u.get('estado', '---')).upper()
            ])
            
        t = Table(table_data, colWidths=[40, 160, 170, 80, 80])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#334155')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'LEFT'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        
        story.append(t)
        doc.build(story)
        
        buffer.seek(0)
        return send_file(
            buffer, 
            as_attachment=True, 
            download_name=f"Reporte de Personal y Operadores Registrados_{datetime.now().strftime('%Y%m%d')}.pdf", 
            mimetype='application/pdf'
        )
        
    except Exception as e:
        print(f"❌ ERROR EN PDF OPERADORES: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
    

# OBERSERVACIONES
@app.route('/admin_observaciones')
def admin_observaciones():
    """Extrae las observaciones vehiculares divididas por estado (Pendiente / Revisado)."""
    rol_usuario = str(session.get('rol', '')).lower().strip()
    if not session.get('usuario_id') or 'administrador' not in rol_usuario: 
        return redirect(url_for('vista_login_admin'))
        
    conexion = None
    try:
        conexion = mysql.connector.connect(**DB_CONFIG)
        cursor = conexion.cursor(dictionary=True)
        
        # Consultar observaciones pendientes (revisado = 0)
        cursor.execute("""
            SELECT id, placa, observacion, 
                   DATE_FORMAT(fecha_registro, '%d/%m/%Y %H:%i:%s') AS fecha_registro 
            FROM observaciones_vehiculos 
            WHERE revisado = 0 
            ORDER BY id DESC
        """)
        pendientes_db = cursor.fetchall()
        
        # Consultar observaciones ya revisadas (revisado = 1)
        cursor.execute("""
            SELECT id, placa, observacion, 
                   DATE_FORMAT(fecha_registro, '%d/%m/%Y %H:%i:%s') AS fecha_registro,
                   DATE_FORMAT(fecha_revision, '%d/%m/%Y %H:%i:%s') AS fecha_revision 
            FROM observaciones_vehiculos 
            WHERE revisado = 1 
            ORDER BY fecha_revision DESC
        """)
        revisados_db = cursor.fetchall()
        
    except mysql.connector.Error as err:
        print(f"❌ Error crítico al leer observaciones: {err}")
        pendientes_db, revisados_db = [], []
    finally:
        if conexion and conexion.is_connected():
            cursor.close()
            conexion.close()
            
    return render_template(
        'admin_observaciones.html', 
        vista='observaciones', 
        pendientes=pendientes_db, 
        revisados=revisados_db
    )


@app.route('/api/revisar_observacion/<int:id_obs>', methods=['POST'])
def revisar_observacion(id_obs):
    """Cambia el estado de una observación a revisado (1) y añade la fecha de revisión actual."""
    rol_usuario = str(session.get('rol', '')).lower().strip()
    if not session.get('usuario_id') or 'administrador' not in rol_usuario: 
        return jsonify({"status": "error", "message": "Acceso denegado. No autorizado."}), 403
        
    conexion = None
    try:
        conexion = mysql.connector.connect(**DB_CONFIG)
        cursor = conexion.cursor()
        
        # Establecemos la zona horaria de Perú para la fecha de revisión
        zona_peru = timezone(timedelta(hours=-5))
        fecha_peru_exacta = datetime.now(zona_peru).strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute("""
            UPDATE observaciones_vehiculos 
            SET revisado = 1, fecha_revision = %s 
            WHERE id = %s
        """, (fecha_peru_exacta, id_obs))
        
        conexion.commit()
        return jsonify({"status": "success", "message": "Registro actualizado con éxito."}), 200
        
    except mysql.connector.Error as err:
        print(f"❌ Error al intentar actualizar ID {id_obs}: {err}")
        return jsonify({"status": "error", "message": str(err)}), 500
    finally:
        if conexion and conexion.is_connected():
            cursor.close()
            conexion.close()

# INCIDENCIAS
@app.route('/admin_incidentes')
def vista_admin_incidentes():
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            # CORRECCIÓN: Solicitamos explícitamente los dos nuevos campos de fecha
            sql = """
                SELECT id, placa, gravedad, descripcion_incidente, estado, 
                       fecha_incidente, fecha_revisionincidente 
                FROM incidentes 
                ORDER BY id DESC
            """
            cursor.execute(sql)
            todos_los_incidentes = cursor.fetchall()
        conexion.close()

        # Formateamos las fechas para los datos cargados mediante Jinja por primera vez
        for inc in todos_los_incidentes:
            if inc['fecha_incidente']:
                inc['fecha_incidente'] = inc['fecha_incidente'].strftime('%d/%m/%Y %H:%M')
            else:
                inc['fecha_incidente'] = 'No disponible'
                
            if inc['fecha_revisionincidente']:
                inc['fecha_revisionincidente'] = inc['fecha_revisionincidente'].strftime('%d/%m/%Y %H:%M')
            else:
                inc['fecha_revisionincidente'] = '--'

        # Separamos los incidentes en dos listas basándonos en su columna 'estado'
        pendientes = [inc for inc in todos_los_incidentes if inc['estado'] == 'Pendiente']
        revisados = [inc for inc in todos_los_incidentes if inc['estado'] == 'Revisado']

        # Al pasar 'pendientes' y 'revisados', Jinja ya puede leerlos dentro del HTML
        return render_template('admin_incidentes.html', pendientes=pendientes, revisados=revisados)

    except Exception as e:
        print(f"❌ Error en vista_admin_incidentes: {str(e)}")
        # Si la base de datos falla, enviamos listas vacías para evitar un crash (Error 500)
        return render_template('admin_incidentes.html', pendientes=[], revisados=[])


@app.route('/api/admin/obtener_incidentes', methods=['GET'])
def api_admin_obtener():
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            # CORRECCIÓN: Agregamos las nuevas columnas a la consulta de la API
            sql = """
                SELECT id, placa, gravedad, descripcion_incidente, estado,
                       fecha_incidente, fecha_revisionincidente 
                FROM incidentes 
                ORDER BY FIELD(estado, 'Pendiente', 'Revisado'), id DESC
            """
            cursor.execute(sql)
            incidentes_admin = cursor.fetchall()
            
        conexion.close()
        
        # PROCESAMIENTO: Formateamos las fechas para que el fetch las lea correctamente
        for inc in incidentes_admin:
            if inc['fecha_incidente']:
                inc['fecha_incidente'] = inc['fecha_incidente'].strftime('%d/%m/%Y %H:%M')
            else:
                inc['fecha_incidente'] = 'No disponible'
                
            if inc['fecha_revisionincidente']:
                inc['fecha_revisionincidente'] = inc['fecha_revisionincidente'].strftime('%d/%m/%Y %H:%M')
            else:
                inc['fecha_revisionincidente'] = '--'

        return jsonify({
            "status": "success",
            "incidentes": incidentes_admin
        }), 200
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/admin/revisar_incidente/<int:id_incidente>', methods=['PUT'])
def api_admin_revisar(id_incidente):
    try:
        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            # OPTIMIZACIÓN: Forzamos la actualización de la fecha de revisión al instante actual
            sql_update = """
                UPDATE incidentes 
                SET estado = 'Revisado', fecha_revisionincidente = NOW() 
                WHERE id = %s
            """
            cursor.execute(sql_update, (id_incidente,))
            filas_afectadas = cursor.rowcount  
            conexion.commit()
            
            if filas_afectadas == 0:
                conexion.close()
                return jsonify({"status": "error", "message": "El incidente no existe."}), 404
                
        conexion.close()
        return jsonify({
            "status": "success", 
            "message": f"El incidente #{id_incidente} ha sido marcado como REVISADO con éxito."
        }), 200
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
#  Panel admin
#  Panel admin





    
#  Panel operador
#  Panel operador
# 1. BITÁCORA DE ACCESOS: Recupera los  ingresos y calcula tiempos
@app.route('/api/obtener_historial_agrupado')
def obtener_historial_agrupado():
    try:
        # 🟢 CONEXIÓN AUTOMÁTICA: Usa tus credenciales centralizadas de DB_CONFIG
        conexion_local = mysql.connector.connect(**DB_CONFIG)
        
        cursor = conexion_local.cursor(dictionary=True)
        
        # 1. Consulta SQL optimizada estrictamente para las 5 columnas de control operativo
        sql = """
            SELECT 
                h.id,
                h.placa,
                DATE_FORMAT(h.fecha_ingreso, '%H:%i:%s') AS hora_entrada,
                DATE_FORMAT(h.fecha_salida, '%H:%i:%s') AS hora_salida,
                DAY(h.fecha_ingreso) AS dia,
                MONTH(h.fecha_ingreso) AS mes,
                YEAR(h.fecha_ingreso) AS anio,
                CASE 
                    WHEN h.fecha_salida IS NULL THEN 'En Cochera'
                    ELSE TIMEDIFF(h.fecha_salida, h.fecha_ingreso)
                END AS tiempo_estadia
            FROM historial_accesos h
            ORDER BY h.fecha_ingreso DESC
        """
        
        cursor.execute(sql)
        registros = cursor.fetchall()
        
        # Cerramos los recursos de inmediato
        cursor.close()
        conexion_local.close()
        
        # 2. RETORNO DIRECTO Y LIMPIO
        # Ya no necesitamos cruzar con SUNARP_SIMULADA en memoria. 
        # Enviamos directo los tiempos operativos al JS de la Bitácora.
        return jsonify(registros)
        
    except Exception as e:
        print(f"❌ Error al procesar historial operativo: {str(e)}")
        return jsonify({"error": str(e)}), 500
    

# 2. LISTAR VEHÍCULOS (INVENTARIO GENERAL OPERADOR)
@app.route('/api/obtener_vehiculos', methods=['GET'])
def obtener_vehiculos():
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # SQL corregido usando exactamente v.id_propietario, ha.id_conductor y c.id
        sql = """
            SELECT 
                v.placa, 
                v.marca, 
                v.modelo, 
                v.color, 
                v.tipo_vehiculo,
                IFNULL(p.nombre_agencia, 'Sin Asignar') AS agencia_propietaria,
                IFNULL(p.telefono_contacto, '---') AS telefono_agencia,
                IFNULL(c.dni_conductor, '---') AS dni_conductor,
                IFNULL(c.nombre_conductor, 'Sin Asignar') AS nombre_conductor,
                IFNULL(c.telefono_conductor, '---') AS telefono_conductor,
                CASE v.id_estado
                    WHEN 1 THEN 'Habilitado'
                    WHEN 2 THEN 'Desactivado'
                    ELSE 'Habilitado'
                END AS estado_unidad,
                CASE 
                    WHEN ln.placa IS NOT NULL THEN 1
                    ELSE 0
                END AS en_lista_negra,
                CASE 
                    WHEN EXISTS (
                        SELECT 1 FROM historial_accesos h 
                        WHERE h.placa = v.placa AND h.fecha_salida IS NULL
                    ) THEN 'Dentro'
                    ELSE 'Fuera'
                END AS ubicacion_cochera
            FROM vehiculos v
            LEFT JOIN propietarios_vehiculos p ON v.id_propietario = p.id
            LEFT JOIN (
                SELECT h1.placa, h1.id_conductor 
                FROM historial_accesos h1
                WHERE h1.id IN (SELECT MAX(id) FROM historial_accesos GROUP BY placa)
            ) ha ON v.placa = ha.placa
            LEFT JOIN conductores c ON ha.id_conductor = c.id
            LEFT JOIN lista_negra ln ON v.placa = ln.placa
            ORDER BY v.placa ASC
        """
        cursor.execute(sql)
        vehiculos = cursor.fetchall()
        
        # Cruce y apoyo con tu diccionario SUNARP_SIMULADA por si la BD física está vacía
        for v in vehiculos:
            placa_limpia = v['placa'].strip().upper()
            if placa_limpia in SUNARP_SIMULADA:
                datos_aux = SUNARP_SIMULADA[placa_limpia]
                if not v['marca'] or v['marca'] == '---': v['marca'] = datos_aux.get('marca', '---')
                if not v['modelo'] or v['modelo'] == '---': v['modelo'] = datos_aux.get('modelo', '---')
                if not v['color'] or v['color'] == '---': v['color'] = datos_aux.get('color', '---')
                if not v['tipo_vehiculo']: v['tipo_vehiculo'] = datos_aux.get('tipo_vehiculo', 'Particular')
                
                if v['agencia_propietaria'] == 'Sin Asignar': 
                    v['agencia_propietaria'] = datos_aux.get('nombre_agencia', 'Sin Asignar')
                if v['telefono_agencia'] == '---': 
                    v['telefono_agencia'] = datos_aux.get('telefono_contacto', '---')
                
                if v['dni_conductor'] == '---':
                    v['dni_conductor'] = datos_aux.get('dni_conductor', '---')
                if v['nombre_conductor'] == 'Sin Asignar': 
                    v['nombre_conductor'] = datos_aux.get('nombre_conductor', 'Sin Asignar')
                if v['telefono_conductor'] == '---': 
                    v['telefono_conductor'] = datos_aux.get('telefono_conductor', '---')

        return jsonify(vehiculos), 200

    except Exception as e:
        print(f"❌ ERROR EN /api/obtener_vehiculos: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
    
# 3. BUSCAR VEHÍCULO INDIVIDUAL POR PLACA
@app.route('/api/buscar_vehiculo/<placa>', methods=['GET'])
def buscar_vehiculo(placa):
    placa = placa.strip().upper()
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        query = """
            SELECT v.placa, v.marca, v.modelo, v.color, v.tipo_vehiculo, v.id_estado,
                   IFNULL(p.nombre_agencia, 'Sin Asignar') AS propietario, 
                   IFNULL(p.telefono_contacto, '---') AS telefono_contacto,
                   CASE WHEN ha.id IS NOT NULL THEN 'Dentro' ELSE 'Fuera' END AS ubicacion_cochera
            FROM vehiculos v
            LEFT JOIN propietarios_vehiculos p ON v.id_propietario = p.id
            LEFT JOIN historial_accesos ha ON v.placa = ha.placa AND ha.fecha_salida IS NULL
            WHERE v.placa = %s AND v.id_estado = 1
        """
        cursor.execute(query, (placa,))
        vehiculo = cursor.fetchone()
        
        # Si no está en la BD, intentamos armar la respuesta desde SUNARP_SIMULADA por si es una consulta al vuelo
        if not vehiculo and placa in SUNARP_SIMULADA:
            datos_s = SUNARP_SIMULADA[placa]
            vehiculo = {
                "placa": placa,
                "marca": datos_s.get("marca"),
                "modelo": datos_s.get("modelo"),
                "color": datos_s.get("color"),
                "tipo_vehiculo": datos_s.get("tipo_vehiculo"),
                "propietario": datos_s.get("nombre_agencia"),
                "telefono_contacto": datos_s.get("telefono_conductor"),
                "ubicacion_cochera": "Fuera",
                "id_estado": 1
            }

        if vehiculo:
            vehiculo['estado'] = 'Habilitado'
            return jsonify(vehiculo), 200
        else:
            return jsonify({"status": "error", "message": "Vehículo inactivo o no registrado."}), 404
            
    except Exception as err: 
        print(f"❌ Error en buscar_vehiculo: {err}")
        return jsonify({"status": "error", "message": str(err)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# 4. GUARDAR UNA NUEVA OBSERVACIÓN (OPERADOR)
@app.route('/api/guardar_observacion', methods=['POST'])
def guardar_observacion():
    conn = None
    cursor = None
    try:
        datos = request.get_json() or {}
        placa = datos.get('placa', '').strip().upper()
        texto_obs = datos.get('observacion', '').strip()

        if not placa or not texto_obs:
            return jsonify({"status": "error", "message": "Placa y observación son requeridas."}), 400

        conn = mysql.connector.connect(**DB_CONFIG)
        # Usamos dictionary=True para manejar de manera más clara la verificación
        cursor = conn.cursor(dictionary=True)
        
        # 1. VALIDACIÓN ESTRICTA: Verificar si el vehículo existe en el inventario general
        # NOTA: Cambia 'vehiculos' por el nombre real de tu tabla de registro de autos si es diferente
        query_verificar = "SELECT placa FROM vehiculos WHERE placa = %s"
        cursor.execute(query_verificar, (placa,))
        vehiculo_existe = cursor.fetchone()

        if not vehiculo_existe:
            return jsonify({
                "status": "error", 
                "message": f"El vehículo con placa '{placa}' no se encuentra registrado en el sistema. Ingrese una placa válida."
            }), 400
        
        # 2. PROCESO DE GUARDADO: Si pasó la validación, procedemos a insertar
        # Forzar hora de Perú exacta
        zona_peru = timezone(timedelta(hours=-5))
        fecha_peru_exacta = datetime.now(zona_peru).strftime('%Y-%m-%d %H:%M:%S')
        
        query_insertar = """
            INSERT INTO observaciones_vehiculos (placa, observacion, fecha_registro, revisado) 
            VALUES (%s, %s, %s, 0)
        """
        cursor.execute(query_insertar, (placa, texto_obs, fecha_peru_exacta))
        conn.commit()
        
        return jsonify({"status": "success", "message": "Observación guardada correctamente."}), 200
        
    except Exception as e:
        print(f"❌ Error en guardar_observacion: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route('/api/obtener_observaciones', methods=['GET'])
def obtener_observaciones():
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        placa = request.args.get('placa', '').strip()
        anio = request.args.get('anio', '').strip()
        mes = request.args.get('mes', '').strip()
        dia = request.args.get('dia', '').strip()

        query = """
            SELECT 
                id, 
                placa, 
                observacion,
                revisado,  #
                DATE_FORMAT(fecha_registro, '%d/%m/%Y') AS dia_formato,
                DATE_FORMAT(fecha_registro, '%H:%i:%s') AS hora_formato
            FROM observaciones_vehiculos
            WHERE 1=1
        """
        params = {}

        if placa:
            query += " AND placa LIKE %(placa)s"
            params['placa'] = f"%{placa}%"
            
        if anio and anio not in ('', 'Todos', 'Todos los años'):
            query += " AND YEAR(fecha_registro) = %(anio)s"
            params['anio'] = int(anio)
            
        if mes and mes not in ('', 'Todos', 'Todos los meses'):
            query += " AND MONTH(fecha_registro) = %(mes)s"
            params['mes'] = int(mes)
            
        if dia and dia not in ('', 'Todos', 'Todos los días'):
            query += " AND DAY(fecha_registro) = %(dia)s"
            params['dia'] = int(dia)

        query += " ORDER BY id DESC"
        
        cursor.execute(query, params)
        resultados = cursor.fetchall()
        return jsonify(resultados), 200
        
    except Exception as e:
        print(f"❌ Error crítico en observaciones: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

# Contingencia registro
@app.route('/api/contingencia_registrar_vehiculo', methods=['POST'])
def contingencia_registrar_vehiculo():
    data = request.get_json()
    
    # 1. Extraer solo los datos esenciales
    placa = data.get('placa', '').strip().upper()
    fecha_manual = data.get('fecha_manual')                
    hora_entrada_manual = data.get('hora_entrada_manual')      
    hora_salida_manual = data.get('hora_salida_manual')        
    
    # Validación estricta de campos del formulario
    if not placa or not fecha_manual or not hora_entrada_manual:
        return jsonify({'status': 'error', 'message': 'Placa, Fecha y Hora de Entrada son campos requeridos.'}), 400

    # Reconstruir los Timestamps
    fecha_ingreso_completa = f"{fecha_manual} {hora_entrada_manual}:00"
    fecha_salida_completa = None
    if hora_salida_manual and hora_salida_manual.strip() != "":
        fecha_salida_completa = f"{fecha_manual} {hora_salida_manual}:00"

    # Valores fijos del sistema para la bitácora
    id_punto_acceso = 1  
    id_operador = 1      

    try:
        conn_local = mysql.connector.connect(**DB_CONFIG)
        cursor = conn_local.cursor()
    except Exception as err_db:
        return jsonify({'status': 'error', 'message': f'Error de conexión con la base de datos: {str(err_db)}'}), 500
    
    try:
        # ---------------------------------------------------------------------
        # PASO A: VERIFICAR SI LA PLACA EXISTE EN LA BD
        # ---------------------------------------------------------------------
        cursor.execute("SELECT placa FROM vehiculos WHERE placa = %s", (placa,))
        vehiculo_existe = cursor.fetchone()
        
        if not vehiculo_existe:
            # Si no existe, enviamos el mensaje de error de inmediato
            return jsonify({
                'status': 'error', 
                'message': 'El vehículo no se encuentra registrado en el sistema. Comuníquese con el administrador.'
            }), 403

        # ---------------------------------------------------------------------
        # PASO B: SI EXISTE, SE VA AUTOMÁTICAMENTE A LA BITÁCORA (historial_accesos)
        # ---------------------------------------------------------------------
        cursor.execute("""
            INSERT INTO historial_accesos (placa, id_punto_acceso, id_operador, id_conductor, fecha_ingreso, fecha_salida)
            VALUES (%s, %s, %s, NULL, %s, %s)
        """, (placa, id_punto_acceso, id_operador, fecha_ingreso_completa, fecha_salida_completa))
        
        conn_local.commit()
        
        return jsonify({
            'status': 'success', 
            'message': f'Vehículo {placa} verificado. Registrado en la bitácora con éxito.'
        })
        
    except Exception as e:
        conn_local.rollback()
        print(f"❌ Error en bitácora de contingencia: {e}")
        return jsonify({'status': 'error', 'message': f'Error interno del servidor: {str(e)}'}), 500
        
    finally:
        cursor.close()
        conn_local.close()

#LISTA NEGRA
@app.route('/api/obtener_lista_negra_operador', methods=['GET'])
def obtener_lista_negra_operador():
    if not session.get('usuario_id'):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 🔥 CORREGIDO: Se seleccionan las columnas reales de tu tabla 'lista_negra'
        sql = """
            SELECT id, placa, motivo_bloqueo, 
                   DATE_FORMAT(fecha_bloqueo, '%d/%m/%Y %H:%i') as fecha_bloqueo 
            FROM lista_negra 
            ORDER BY id DESC
        """
        cursor.execute(sql)
        vehiculos = cursor.fetchall()
        cursor.close()

        return jsonify({
            'status': 'success',
            'vehiculos': vehiculos
        })
    except Exception as e:
        print(f"❌ Error al obtener lista negra: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if conn and conn.is_connected():
            conn.close()

# INCIDENCIAS
@app.route('/api/operador/registrar_incidente', methods=['POST'])
def api_operador_registrar():
    try:
        data = request.get_json()
        placa = data.get('placa')
        gravedad = data.get('gravedad')
        descripcion = data.get('descripcion_incidente')

        if not placa or not gravedad or not descripcion:
            return jsonify({"status": "error", "message": "Faltan campos obligatorios."}), 400

        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            # 1. Validamos directamente si la placa existe en la tabla vehiculos
            cursor.execute("SELECT placa FROM vehiculos WHERE placa = %s", (placa,))
            resultado_vehiculo = cursor.fetchone()

            if not resultado_vehiculo:
                conexion.close()
                return jsonify({"status": "error", "message": f"La placa {placa} no se encuentra registrada en el sistema."}), 404

            # 2. Insertamos usando directamente el campo 'placa' en la tabla incidentes
            sql_insert = """
                INSERT INTO incidentes (placa, descripcion_incidente, gravedad, estado) 
                VALUES (%s, %s, %s, 'Pendiente')
            """
            cursor.execute(sql_insert, (placa, descripcion, gravedad))
            conexion.commit()

        conexion.close()
        return jsonify({"status": "success", "message": "Incidente registrado con éxito."}), 201

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/operador/obtener_incidentes', methods=['GET'])
def api_operador_obtener():
    try:
        # Capturamos filtros y limpiamos espacios vacíos
        placa_filtro = request.args.get('placa', '').strip()
        estado_filtro = request.args.get('estado', '').strip()

        conexion = obtener_conexion()
        with conexion.cursor(dictionary=True) as cursor:
            # CORRECCIÓN: Agregamos las nuevas columnas de fecha a la consulta SQL
            sql = """
                SELECT id, placa, gravedad, descripcion_incidente, estado, 
                       fecha_incidente, fecha_revisionincidente 
                FROM incidentes 
                WHERE 1=1
            """
            params = []

            # Filtro por placa (si escriben algo)
            if placa_filtro:
                sql += " AND placa LIKE %s"
                params.append(f"%{placa_filtro}%")
            
            # Filtro por estado
            if estado_filtro:
                sql += " AND estado LIKE %s"
                params.append(f"{estado_filtro}")

            # Ordenación por estado y por ID descendente
            sql += " ORDER BY FIELD(estado, 'Pendiente', 'Revisado'), id DESC"
            
            cursor.execute(sql, params)
            lista_incidentes = cursor.fetchall()
            
        conexion.close()
        
        # PROCESAMIENTO: Formateamos los objetos datetime de MySQL a texto legible antes de enviarlos al HTML
        for inc in lista_incidentes:
            if inc['fecha_incidente']:
                inc['fecha_incidente'] = inc['fecha_incidente'].strftime('%d/%m/%Y %H:%M')
            else:
                inc['fecha_incidente'] = 'No disponible'
                
            if inc['fecha_revisionincidente']:
                inc['fecha_revisionincidente'] = inc['fecha_revisionincidente'].strftime('%d/%m/%Y %H:%M')
            else:
                inc['fecha_revisionincidente'] = '--' # Línea limpia si sigue en estado Pendiente
        
        return jsonify({
            "status": "success",
            "incidentes": lista_incidentes
        }), 200
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
#  Panel operador
#  Panel operador




#  CAMARA/API
#  SUNARP SIMULADA: Diccionario local de pruebas que imita las 
# respuestas de la API oficial de Registros Públicos.
SUNARP_SIMULADA = {
"P4G-564": {
        "marca": "TOYOTA", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "GERENCIA MANCOMUNIDAD",
        "telefono_contacto": "968451235",
        "nombre_conductor": "ADRIANO RIVAS",
        "dni_conductor": "45781234",
        "telefono_conductor": "951753462"
    },
    "P5A-817": {
        "marca": "NISSAN", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "GERENCIA REGIONAL NORTE 2",
        "telefono_contacto": "942187653",
        "nombre_conductor": "JOSE ABAD",
        "dni_conductor": "76770718",
        "telefono_conductor": "966321458"
    },
    "P3X-826": {
        "marca": "MITSUBISHI", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "COBRANZA",
        "telefono_contacto": "974125893",
        "nombre_conductor": "IVAN GALLEGO",
        "dni_conductor": "41253698",
        "telefono_conductor": "981472536"
    },
    "P3X-814": {
        "marca": "TOYOTA", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "CIN",
        "telefono_contacto": "951487236",
        "nombre_conductor": "CLAUDIO SANDOVAL",
        "dni_conductor": "02415879",
        "telefono_conductor": "942581637"
    },
    "P3X-785": {
        "marca": "NISSAN", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "COBRANZA",
        "telefono_contacto": "974125893",
        "nombre_conductor": "LUIS MARTINEZ",
        "dni_conductor": "43698521",
        "telefono_conductor": "968521473"
    },
    "P3X-761": {
        "marca": "MITSUBISHI", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "CASTILLA",
        "telefono_contacto": "963258147",
        "nombre_conductor": "FRANCISCO CAMINO",
        "dni_conductor": "10254789",
        "telefono_conductor": "974152638"
    },
    "P3X-764": {
        "marca": "TOYOTA", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "LIBERTAD",
        "telefono_contacto": "954123687",
        "nombre_conductor": "LUIS MIO",
        "dni_conductor": "40258963",
        "telefono_conductor": "914725836"
    },
    "P3X-786": {
        "marca": "NISSAN", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "CASTILLA",
        "telefono_contacto": "963258147",
        "nombre_conductor": "CARLOS CARRASCO",
        "dni_conductor": "46985214",
        "telefono_conductor": "936251478"
    },
    "P3X-759": {
        "marca": "MITSUBISHI", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "SANCHEZ CERRO",
        "telefono_contacto": "981245763",
        "nombre_conductor": "MIGUEL GARCES",
        "dni_conductor": "03259874",
        "telefono_conductor": "925634178"
    },
    "P3X-763": {
        "marca": "TOYOTA", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "MERCADO",
        "telefono_contacto": "941258763",
        "nombre_conductor": "RAFAEL CABREDO",
        "dni_conductor": "42158796",
        "telefono_conductor": "987451236"
    },
    "P3X-757": {
        "marca": "NISSAN", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "GERENCIA REGIONAL NORTE 1",
        "telefono_contacto": "962154873",
        "nombre_conductor": "GUILLERMO BAZAN",
        "dni_conductor": "10369852",
        "telefono_conductor": "954128763"
    },
    "P3X-760": {
        "marca": "MITSUBISHI", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "CATACAOS",
        "telefono_contacto": "941526378",
        "nombre_conductor": "ANTONIO PIZARRO",
        "dni_conductor": "02807451",
        "telefono_conductor": "996325814"
    },
    "P3H-764": {
        "marca": "TOYOTA", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "LAS LOMAS",
        "telefono_contacto": "955142368",
        "nombre_conductor": "JUNIOR MONTALBAN",
        "dni_conductor": "47852163",
        "telefono_conductor": "921478536"
    },
    "P3X-753": {
        "marca": "NISSAN", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "CAMIONETA",
        "nombre_agencia": "SECHURA",
        "telefono_contacto": "968512437",
        "nombre_conductor": "ABRAHAM SILVA",
        "dni_conductor": "41258963",
        "telefono_conductor": "985632147"
    },
    "M1R-922": {
        "marca": "HONDA", 
        "modelo": "HILUX", 
        "color": "BLANCO", 
        "tipo_vehiculo": "MOTOCICLETA",
        "nombre_agencia": "COBRANZA",
        "telefono_contacto": "974125893",
        "nombre_conductor": "NEIBER S. / GUILLERMO D.",
        "dni_conductor": "48521369",
        "telefono_conductor": "934152687"
    }
}


#  REGISTRO AUTOMÁTICO AUXILIAR: Guarda directamente en la BD los datos 
# de texto de un vehículo obtenidos desde una consulta automática.
def registrar_vehiculo_automatico_db(cursor, conn, placa, datos_api):
    try:
        # Corregido: Buscamos por nombre_agencia (que es lo que devuelve el mock/API para la empresa propietaria)
        cursor.execute("SELECT id FROM propietarios_vehiculos WHERE nombre_agencia = %s", (datos_api['propietario'],))
        existe_prop = cursor.fetchone()
        
        while cursor.nextset():
            pass

        if existe_prop:
            id_propietario = existe_prop['id'] if isinstance(existe_prop, dict) else existe_prop[0]
        else:
            # Corregido: La tabla propietarios_vehiculos NO tiene columna 'dni'. Usamos su estructura real.
            cursor.execute(
                "INSERT INTO propietarios_vehiculos (nombre_agencia, telefono_contacto) VALUES (%s, '---')",
                (datos_api['propietario'],)
            )
            id_propietario = cursor.lastrowid
            
            while cursor.nextset():
                pass

        sql = """
            INSERT INTO vehiculos (placa, id_propietario, marca, modelo, color, tipo_vehiculo, id_estado) 
            VALUES (%s, %s, %s, %s, %s, %s, 1) 
            ON DUPLICATE KEY UPDATE 
                id_propietario = VALUES(id_propietario),
                marca = VALUES(marca),
                modelo = VALUES(modelo),
                color = VALUES(color),
                tipo_vehiculo = VALUES(tipo_vehiculo),
                id_estado = 1
        """
        valores = (
            placa, 
            id_propietario, 
            datos_api['marca'], 
            datos_api['modelo'], 
            datos_api['color'], 
            datos_api['tipo_vehiculo']
        )
        cursor.execute(sql, valores)
        print(f"✔️ [MYSQL] Vehículo {placa} sincronizado.")
        
    except Exception as e:
        print(f"❌ Error en registrar_vehiculo_automatico_db: {str(e)}")
        raise e
    


@app.route('/api/operador_completar_vehiculo', methods=['POST'])
def operador_completar_vehiculo():
    datos = request.json
    placa = datos.get('placa')
    propietario = datos.get('propietario') # Nombre de la agencia
    marca = datos.get('marca')
    modelo = datos.get('modelo')
    color = datos.get('color')
    tipo_vehiculo = datos.get('tipo_vehiculo')

    if not placa:
        return jsonify({"status": "error", "message": "Falta el número de placa."}), 400

    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        # 1. Verificar si el vehículo ya existe (Traemos nombre_agencia en vez del DNI inexistente)
        cursor.execute("""
            SELECT v.id_propietario, p.nombre_agencia 
            FROM vehiculos v
            LEFT JOIN propietarios_vehiculos p ON v.id_propietario = p.id
            WHERE v.placa = %s OR v.placa = %s
        """, (placa.replace("-",""), placa))
        vehiculo_actual = cursor.fetchone()

        while cursor.nextset(): pass

        if not vehiculo_actual:
            return jsonify({"status": "error", "message": "La placa no ha sido leída inicialmente por el sistema."}), 404

        id_propietario = vehiculo_actual['id_propietario']

        # 2. Manejo e inserción/actualización del Propietario (Agencia)
        if id_propietario is None:
            # Crear nueva agencia si estaba en NULL
            cursor.execute("""
                INSERT INTO propietarios_vehiculos (nombre_agencia, telefono_contacto) 
                VALUES (%s, '---')
            """, (propietario,))
            id_propietario = cursor.lastrowid
        else:
            # Actualizamos el nombre de la agencia asociada
            cursor.execute("""
                UPDATE propietarios_vehiculos 
                SET nombre_agencia = %s 
                WHERE id = %s
            """, (propietario, id_propietario))

        while cursor.nextset(): pass

        # 3. Actualizamos los campos restantes del vehículo
        cursor.execute("""
            UPDATE vehiculos 
            SET id_propietario = %s, marca = %s, modelo = %s, color = %s, tipo_vehiculo = %s
            WHERE placa = %s OR placa = %s
        """, (id_propietario, marca, modelo, color, tipo_vehiculo, placa.replace("-",""), placa))
        
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"status": "success", "message": "Datos de matrícula guardados y validados correctamente."}), 200

    except Exception as e:
        if cursor: cursor.close()
        if conn: conn.close()
        return jsonify({"status": "error", "message": str(e)}), 500



@app.route('/api/procesar_placa_imagen', methods=['POST'])
def procesar_placa_imagen():
    if 'foto_placa' not in request.files:
        return jsonify({"status": "error", "message": "Archivo no enviado."}), 400
        
    archivo = request.files['foto_placa']
    if archivo.filename == '': 
        return jsonify({"status": "error", "message": "Captura inválida."}), 400

    conn = None
    cursor = None
    try:
        # 📸 1. DECODIFICACIÓN DE IMAGEN Y PROCESAMIENTO OCR
        imagen_np = np.frombuffer(archivo.read(), np.uint8)
        fotograma = cv2.imdecode(imagen_np, cv2.IMREAD_COLOR)
        if fotograma is None: 
            return jsonify({"status": "error", "message": "Imagen ilegible o corrupta."}), 400

        gris = cv2.cvtColor(fotograma, cv2.COLOR_BGR2GRAY)
        resultados = lector_ocr.readtext(gris, paragraph=False)
        
        texto_detectado = ""
        for (caja, texto, confianza) in resultados:
            letra_limpia = re.sub(r'[^A-Z0-9-]', '', texto.upper().strip())
            letra_limpia = letra_limpia.replace("-", "").replace(" ", "")
            if letra_limpia in ["PERU", "PE", "PERÚ"]:
                continue
            if len(letra_limpia) >= 5: 
                texto_detectado = letra_limpia
                break
                
        if not texto_detectado:
            return jsonify({"status": "error", "message": "No se reconoció un patrón de matrícula válido."}), 400

        placa_sin_guion = texto_detectado.replace("-", "").strip().upper()
        placa_con_guion = placa_sin_guion if len(placa_sin_guion) < 4 else f"{placa_sin_guion[:3]}-{placa_sin_guion[3:]}"

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 🛡️ 2. CONTROL DE LISTA NEGRA
        cursor.execute("SELECT motivo_bloqueo FROM lista_negra WHERE placa = %s OR placa = %s", (placa_sin_guion, placa_con_guion))
        bloqueado = cursor.fetchone()
        while cursor.nextset(): pass

        es_lista_negra = False
        mensaje_notificacion = ""
        
        if bloqueado:
            es_lista_negra = True
            mensaje_notificacion = f"🚨 ACCESO DENEGADO: Vehículo en LISTA NEGRA. Motivo: {bloqueado['motivo_bloqueo']}"

        id_nuevo_acceso = 0
        tipo_movimiento_ia = "Bloqueado"
        tiempo_estancia = "---"
        ruta_imagen_simulada = f"uploads/capturas/{archivo.filename}"

        if es_lista_negra:
            datos_evento = {
                "id_acceso": 0,
                "placa": placa_con_guion,
                "vehiculo_oficial": True, # Forzado como oficial para que ejecute el bloqueo directo
                "registrado": False, 
                "tipo_movimiento": "Bloqueado", 
                "tiempo_estancia": "---",
                "message": mensaje_notificacion,
                "ruta_imagen": ruta_imagen_simulada
            }
        else:
            # 🔍 3. BÚSQUEDA EXCLUSIVA EN TU TABLA DE VEHÍCULOS OFICIALES
            cursor.execute("""
                SELECT v.placa, v.marca, v.modelo, v.color, v.tipo_vehiculo, v.id_estado
                FROM vehiculos v
                WHERE REPLACE(v.placa, '-', '') = %s AND v.id_estado = 1
                LIMIT 1
            """, (placa_sin_guion,))
            vehiculo = cursor.fetchone()
            while cursor.nextset(): pass
            
            # -------------------------------------------------------------------------
            # CASO A: NO ES VEHÍCULO OFICIAL (No existe en tu tabla de MySQL)
            # -------------------------------------------------------------------------
            if not vehiculo:
                # FRENAMOS EL FLUJO: No insertamos nada en la base de datos de accesos ni vehículos
                datos_evento = {
                    "id_acceso": 0,
                    "placa": placa_con_guion,
                    "vehiculo_oficial": False, # <-- Esto activa los botones [Sí] o [No] en JavaScript
                    "registrado": True,
                    "tipo_movimiento": "Entrada",
                    "tiempo_estancia": "---",
                    "message": "Vehículo nuevo detectado. Requiere autorización.",
                    "ruta_imagen": ruta_imagen_simulada
                }
            
            # -------------------------------------------------------------------------
            # CASO B: SÍ ES VEHÍCULO OFICIAL (Existe en tu tabla de MySQL)
            # -------------------------------------------------------------------------
            else:
                # ⏱️ COMPUTAR ENTRADA O SALIDA AUTOMÁTICA EN HISTORIAL
                cursor.execute("""
                    SELECT id, fecha_ingreso FROM historial_accesos 
                    WHERE REPLACE(placa, '-', '') = %s AND fecha_salida IS NULL 
                    ORDER BY id DESC LIMIT 1
                """, (placa_sin_guion,))
                registro_activo = cursor.fetchone()
                while cursor.nextset(): pass

                id_operador_sesion = session.get('usuario_id', 1) or 1

                if registro_activo:
                    id_nuevo_acceso = registro_activo['id']
                    fecha_ingreso = registro_activo['fecha_ingreso']
                    diferencia = datetime.now() - fecha_ingreso
                    total_segundos = diferencia.total_seconds()
                    horas = int(total_segundos // 3600)
                    minutos = int((total_segundos % 3600) // 60)
                    tiempo_estancia = f"{horas}h {minutos}m" if horas > 0 else f"{minutos} min"
                    
                    cursor.execute("UPDATE historial_accesos SET fecha_salida = NOW() WHERE id = %s", (id_nuevo_acceso,))
                    tipo_movimiento_ia = "Salida"
                    mensaje_notificacion = "Salida registrada automáticamente."
                else:
                    cursor.execute("""
                        INSERT INTO historial_accesos (placa, id_punto_acceso, id_operador, fecha_ingreso, fecha_salida)
                        VALUES (%s, 1, %s, NOW(), NULL)
                    """, (placa_con_guion, id_operador_sesion))
                    id_nuevo_acceso = cursor.lastrowid
                    tipo_movimiento_ia = "Entrada"
                    mensaje_notificacion = "Ingreso registrado automáticamente por lectura IA."

                conn.commit()

                # PERSISTENCIA DE CAPTURA
                cursor.execute("INSERT INTO capturas_ia (id_acceso, ruta_imagen) VALUES (%s, %s)", (id_nuevo_acceso, ruta_imagen_simulada))
                conn.commit()

                datos_evento = {
                    "id_acceso": id_nuevo_acceso,
                    "placa": placa_con_guion, 
                    "vehiculo_oficial": True, # <-- Flujo normal directo automático
                    "registrado": True, 
                    "tipo_movimiento": tipo_movimiento_ia, 
                    "tiempo_estancia": tiempo_estancia,
                    "message": mensaje_notificacion,
                    "ruta_imagen": ruta_imagen_simulada
                }
                
        cursor.close()
        conn.close()
        
        # Enviar en vivo al stream de la interfaz web
        cola_detecciones.put(datos_evento) 
        
        return jsonify({
            "status": "success", 
            "is_blocked": es_lista_negra,
            "message": datos_evento["message"], 
            "datos": datos_evento
        }), 200

    except Exception as e:
        print(f"❌ ERROR GENERAL EN RUTA IMAGEN: {e}")
        if cursor: cursor.close()
        if conn: conn.close()
        return jsonify({"status": "error", "message": str(e)}), 500


#  MOTOR DE STREAMING DE VIDEO Y EVENTOS EN TIEMPO REAL 
#  CAMARA STREAM: Captura los fotogramas de la webcam local y 
# los transmite continuamente en formato JPG.
# 1. VARIABLES GLOBALES (Para compartir los ojos de la cámara con la IA sin lag)


def asistente_ia_segundo_plano():
    global ultimo_frame_camara, ULTIMAS_DETECCIONES_IA, lock_frame, cola_detecciones
    print("[🚀 IA OPTIMIZADA] Motor de asistencia vehicular con estabilización por ráfaga en marcha.")
    
    TIEMPO_COOLDOWN = 3.0  # Margen de maniobra anti-saturación
    
    # Expresión regular estándar para placas peruanas/latinoamericanas:
    # Acepta combinaciones de 3 letras y 3 números con o sin guion (ej: P4G-564, P3X759, M1R922)
    PATRON_PLACA = r'^[A-Z0-9]{3}-?[A-Z0-9]{3}$|^[A-Z0-9]{2}-?[A-Z0-9]{4}$'
    
    while True:
        # 1. ESPACIO PARA RESPIRAR: Procesamos ~3 cuadros por segundo (0.35s libera CPU y estabiliza)
        time.sleep(0.15)
        
        frame_original = None
        with lock_frame:
            if ultimo_frame_camara is not None:
                frame_original = ultimo_frame_camara.copy()
        
        if frame_original is None:
            continue
            
        try:
            alto, ancho = frame_original.shape[:2]
            frame_reducido = cv2.resize(frame_original, (int(ancho / 2), int(alto / 2)))
            gris = cv2.cvtColor(frame_reducido, cv2.COLOR_BGR2GRAY)
            resultados = lector_ocr.readtext(gris, paragraph=False)
            
            placa_candidata = ""
            for (caja, texto, confianza) in resultados:
                letra_limpia = re.sub(r'[^A-Z0-9-]', '', texto.upper().strip())
                letra_limpia = letra_limpia.replace("-", "").replace(" ", "")
                if letra_limpia in ["PERU", "PE", "PERÚ", "PUBLICO", "SERVICIO"]:
                    continue
                if len(letra_limpia) >= 5 and len(letra_limpia) <= 8: 
                    placa_candidata = letra_limpia
                    break
            
            # Si el fotograma está vacío o no leyó nada con la longitud correcta, saltamos
            if not placa_candidata:
                continue
                
            # Filtro estricto por formato (Regex). Si lee ruidos como "VOMNCC" o "AOUCC", se descartan aquí
            if not re.match(PATRON_PLACA, placa_candidata):
                continue

            # 2. ACUMULACIÓN EN EL BUFFER DE ESTABILIZACIÓN
            RAFAGA_DETECCIONES.append(placa_candidata)
            if len(RAFAGA_DETECCIONES) > 5:  # Evaluamos bloques de máximo 5 lecturas
                RAFAGA_DETECCIONES.pop(0)

            if len(RAFAGA_DETECCIONES) >= 3:
                # Contamos la consistencia de las lecturas en la ráfaga
                conteo = Counter(RAFAGA_DETECCIONES)
                placa_confirmada, veces_repetida = conteo.most_common(1)[0]
                
                # CRITERIO DE VERACIDAD: Solo avanza si se leyó exactamente igual mínimo 3 veces
                if veces_repetida < 3:
                    continue  # Es ruido visual errático, seguimos esperando estabilidad
                
                # Guardamos la placa confirmada limpia
                placa_sin_guion = placa_confirmada
            else:
                continue

            # 3. FILTRO DE COOLDOWN (Evita registrar la misma placa real en bucle)
            ahora = time.time()
            if placa_sin_guion in ULTIMAS_DETECCIONES_IA:
                if ahora - ULTIMAS_DETECCIONES_IA[placa_sin_guion] < TIEMPO_COOLDOWN:
                    continue  
            
            # Confirmada la lectura y fuera del cooldown, limpiamos ráfaga para el siguiente carro
            ULTIMAS_DETECCIONES_IA[placa_sin_guion] = ahora
            RAFAGA_DETECCIONES.clear()
            
            print(f"👁️ [IA ESTABLE] Placa plenamente confirmada: {placa_sin_guion}")
            
            # Formateamos con guion estético para visualización
            placa_con_guion = placa_sin_guion if len(placa_sin_guion) < 4 else f"{placa_sin_guion[:3]}-{placa_sin_guion[3:]}"
            
            # Conexión a Base de Datos
            conn_live = mysql.connector.connect(**DB_CONFIG)
            cursor_live = conn_live.cursor(dictionary=True)
            
            # ----------------=================================================
            # ESCENARIO 1: CONTROL DE LISTA NEGRA
            # ----------------=================================================
            cursor_live.execute("SELECT motivo_bloqueo FROM lista_negra WHERE placa = %s OR placa = %s", (placa_sin_guion, placa_con_guion))
            bloqueado = cursor_live.fetchone()
            while cursor_live.nextset(): pass
            
            if bloqueado:
                datos_evento = {
                    "placa": placa_con_guion,
                    "vehiculo_oficial": True,
                    "tipo_movimiento": "Bloqueado",
                    "registrado": False,
                    "agencia": "SISTEMA DE SEGURIDAD",
                    "detalles_vehiculo": "ACCESO PROHIBIDO",
                    "message": f"🚨 VEHÍCULO EN LISTA NEGRA: {bloqueado['motivo_bloqueo']}"
                }
            else:
                # ----------------=================================================
                # ESCENARIO 2: BUSCAR SI PERTENECE AL INVENTARIO CON UN JOIN A AGENCIA
                # ----------------=================================================
                cursor_live.execute("""
                    SELECT v.placa, v.marca, v.modelo, v.color, p.nombre_agencia 
                    FROM vehiculos v
                    LEFT JOIN propietarios_vehiculos p ON v.id_propietario = p.id
                    WHERE (REPLACE(v.placa, '-', '') = %s) AND v.id_estado = 1
                    LIMIT 1
                """, (placa_sin_guion,))
                db_vehiculo = cursor_live.fetchone()
                while cursor_live.nextset(): pass
                
                # CASO A: EL VEHÍCULO NO EXISTE (Vehículo Nuevo / Desconocido)
                if not db_vehiculo:
                    datos_evento = {
                        "placa": placa_con_guion,
                        "vehiculo_oficial": False,  # <-- JavaScript activa los botones interactivos [Sí / No]
                        "tipo_movimiento": "Entrada",
                        "registrado": False,
                        "agencia": "Desconocido",
                        "detalles_vehiculo": "No registrado en inventario",
                        "message": "Vehículo nuevo detectado frente a la cámara."
                    }
                
                # CASO B: EL VEHÍCULO SÍ ES OFICIAL (Existe en la base de datos y está activo)
                else:
                    # Gestión automática de Entradas/Salidas en el historial
                    cursor_live.execute("""
                        SELECT id FROM historial_accesos 
                        WHERE REPLACE(placa, '-', '') = %s AND fecha_salida IS NULL 
                        ORDER BY id DESC LIMIT 1
                    """, (placa_sin_guion,))
                    registro_activo = cursor_live.fetchone()
                    while cursor_live.nextset(): pass
                    
                    if registro_activo:
                        cursor_live.execute("UPDATE historial_accesos SET fecha_salida = NOW() WHERE id = %s", (registro_activo['id'],))
                        tipo_movimiento_ia = "Salida"
                        mensaje_notificacion = "Salida automática registrada por cámara."
                    else:
                        cursor_live.execute("""
                            INSERT INTO historial_accesos (placa, id_punto_acceso, id_operador, fecha_ingreso, fecha_salida) 
                            VALUES (%s, 1, 1, NOW(), NULL)
                        """, (placa_con_guion,))
                        tipo_movimiento_ia = "Entrada"
                        mensaje_notificacion = "Ingreso automático registrado por cámara."
                    
                    conn_live.commit()
                    
                    datos_evento = {
                        "placa": placa_con_guion,
                        "vehiculo_oficial": True,       # <-- JavaScript procesa de forma directa y limpia
                        "tipo_movimiento": tipo_movimiento_ia,
                        "registrado": True,
                        "agencia": db_vehiculo['nombre_agencia'] if db_vehiculo['nombre_agencia'] else "Sin Agencia Asignada",
                        "detalles_vehiculo": f"{db_vehiculo['marca']} {db_vehiculo['modelo']} ({db_vehiculo['color']})",
                        "message": mensaje_notificacion
                    }
            
            cursor_live.close()
            conn_live.close()
            
            # Envío directo al stream SSE para actualizar el panel del operador
            cola_detecciones.put(datos_evento)

        except Exception as live_err:
            print(f"⚠️ Error crítico en hilo de asistencia de IA: {live_err}")

# 3. TRABAJADOR A (Muestra el video en la pantalla súper fluido sin detenerse jamás)
def generar_frames_camara():
    global ultimo_frame_camara
    camara = cv2.VideoCapture(0)
    if not camara.isOpened():
        print("❌ Error: No se pudo acceder a la cámara local.")
        return

    while True:
        exito, frame = camara.read()
        if not exito:
            break
        
        with lock_frame:
            ultimo_frame_camara = frame.copy()

        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
               
    camara.release()

@app.route('/api/ultimo_acceso_ia')
def ultimo_acceso_ia():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # 🔍 CORREGIDO: p.nombre_completo -> p.nombre_agencia / p.dni -> p.telefono_contacto
        sql = """
            SELECT h.id AS id_acceso, h.placa, h.fecha_salida,
                   IFNULL(v.marca, '---') AS marca, IFNULL(v.modelo, '---') AS modelo, 
                   IFNULL(v.color, '---') AS color, IFNULL(v.tipo_vehiculo, 'Particular') AS tipo_vehiculo,
                   IFNULL(p.nombre_agencia, 'Sin Asignar') AS propietario, IFNULL(p.telefono_contacto, '---') AS telefono_propietario
            FROM historial_accesos h
            LEFT JOIN vehiculos v ON REPLACE(h.placa, '-', '') = REPLACE(v.placa, '-', '')
            LEFT JOIN propietarios_vehiculos p ON v.id_propietario = p.id
            ORDER BY h.id DESC LIMIT 1
        """
        cursor.execute(sql)
        ultimo = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if ultimo:
            tipo_mov = "Salida" if ultimo['fecha_salida'] else "Entrada"
            mensaje = f"Placa {ultimo['placa']} procesada. Registro de {tipo_mov} detectado."
            
            return jsonify({
                "placa": ultimo['placa'],
                "propietario": ultimo['propietario'],
                "telefono_propietario": ultimo['telefono_propietario'],
                "marca": ultimo['marca'],
                "modelo": ultimo['modelo'],
                "tipo_vehiculo": ultimo['tipo_vehiculo'],
                "color": ultimo['color'],
                "tipo_movimiento": tipo_mov,
                "registrado": True,
                "message": mensaje
            }), 200
        else:
            return jsonify({"message": "No hay registros previos en la bitácora"}), 404
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/video_feed')
def video_feed():
    return Response(generar_frames_camara(), mimetype='multipart/x-mixed-replace; boundary=frame')


# 4. TRANSMISIÓN SSE PARA CONECTAR AL CELULAR/PÁGINA
@app.route('/api/stream_placas_detectadas')
def stream_placas_detectadas():
    def generar_eventos():
        while True:
            try:
                # Espera una placa por máximo 5 segundos
                datos = cola_detecciones.get(timeout=5.0)
                yield f"data: {json.dumps(datos)}\n\n"
            except queue.Empty:
                # 💡 SI NO HAY PLACAS: Mandamos un comentario vacío (ping) 
                # Esto mantiene la conexión abierta y evita que JavaScript se corte solo
                yield ": ping\n\n"
    
    return Response(generar_eventos(), mimetype='text/event-stream')

# 5. DISPARADOR INMEDIATO DEL HILO (Mantiene el sistema despierto en segundo plano)
hilo_ia = threading.Thread(target=asistente_ia_segundo_plano, daemon=True)
hilo_ia.start()




#  ARRANQUE OFICIAL DEL SERVIDOR
   
  

if __name__ == '__main__':
    # host='0.0.0.0' le permite a tu celular conectarse usando la IP de la PC
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)