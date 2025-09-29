import logging
import os
import json
from typing import Final 
import asyncio 
import sys 

# Configurar logging al inicio
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- CRITICAL FIX: Monkey Patching para Gevent/Gunicorn ---
from gevent import monkey
import gevent # <-- Importar gevent para usar gevent.spawn
# Asegúrate de que esto se ejecute lo antes posible.
monkey.patch_all(subprocess=False) 
# --------------------------------------------------------

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore 

# --- FLASK Imports ---
from flask import Flask, request, jsonify 

# --- Imports de Telegram ---
import telegram 
from telegram import Update 
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes 

# Importa tus utilidades y handlers
from handlers import start, style_selected, dithering_colors_selected, photo_handler, show_credits, buy_credits_callback, help_command, paypal_confirm_callback, handle_paypal_webhook 
from db_utils import get_firestore_client 

# --- CONSTANTES ---
TOKEN: Final = os.environ.get("TELEGRAM_BOT_TOKEN")
MAX_FREE_CREDITS: Final = 5 
WATERMARK_TEXT: Final = "FREE PIXELATION | @PixelFusionBot" 
MAX_IMAGE_SIZE_BYTES: Final = 2 * 1024 * 1024 # 2 MB
PAYPAL_CLIENT_ID: Final = os.environ.get("PAYPAL_CLIENT_ID", "SIMULATED_ID")
PAYPAL_CLIENT_SECRET: Final = os.environ.get("PAYPAL_CLIENT_SECRET", "SIMULATED_SECRET")
# RENDER_URL debe ser tu URL base: https://pixelfusion-4m8v.onrender.com
RENDER_URL: Final = os.environ.get("RENDER_EXTERNAL_URL") 
if not RENDER_URL:
    logging.critical("RENDER_EXTERNAL_URL no está definida. La configuración del webhook fallará.")


# ==========================================================
# INICIALIZACIÓN DE FLASK (Servidor Webhook)
# ==========================================================

app_flask = Flask(__name__)
# Variables globales para la app de Telegram y el estado de inicialización
app_tg = None 
webhook_checked = False # Bandera para controlar la verificación del webhook

# --- FUNCIÓN AUXILIAR PARA CORRER CÓDIGO ASÍNCRONO DENTRO DE GEVENT ---
def run_async_task(coroutine):
    """Ejecuta una corutina en el thread/greenlet actual de forma síncrona."""
    # Usamos asyncio.run para asegurar que la corutina se ejecute
    try:
        asyncio.run(coroutine)
    except RuntimeError as e:
        # Esto puede ocurrir si un event loop ya está corriendo (común con monkey patching), 
        # intentamos ejecutarlo en el loop existente
        if 'Event loop is closed' in str(e) or 'Cannot run the event loop' in str(e):
             # Si el loop está cerrado o no se puede iniciar, lo ignoramos o manejamos si es crítico
             logging.warning(f"Ignorando RuntimeError en run_async_task: {e}")
             pass
        else:
             logging.error(f"Error inesperado al correr la corutina: {e}")
             raise e
# ----------------------------------------------------------------------


@app_flask.route('/', methods=['GET'])
async def health_check_endpoint(): 
    """
    Endpoint de Health Check para Render.
    Chequea y configura el webhook UNA SOLA VEZ por worker para evitar errores de bucle cerrado.
    """
    global webhook_checked 
    
    if app_tg is None:
        return "Bot not ready (TG app is None)", 500

    # Ejecutar la verificación del webhook solo si aún no se ha hecho en este worker.
    if not webhook_checked:
        try:
            # Aseguramos la inicialización del PTB en este worker
            # Nota: Necesitamos usar initialize() aunque estemos en un worker de gunicorn/gevent.
            await app_tg.initialize() 
            webhook_url = f"{RENDER_URL}/telegram_webhook"
            webhook_info = await app_tg.bot.get_webhook_info()
            
            # 1. Comprobar si la URL actual es la correcta
            if webhook_info.url != webhook_url:
                logging.warning(f"Webhook INCORRECTO: Telegram tiene '{webhook_info.url}'. Intentando corregir a '{webhook_url}'.")
                
                # Intentar corregir/configurar el webhook
                await app_tg.bot.delete_webhook()
                await app_tg.bot.set_webhook(url=webhook_url)
                logging.info(f"*** Webhook CORREGIDO y configurado en: {webhook_url} ***")
            else:
                logging.info(f"Webhook OK: La URL actual registrada en Telegram es correcta: {webhook_info.url}")
            
            webhook_checked = True # Marcar como verificado después del éxito

        except Exception as e:
            # Si falla, simplemente logueamos, pero NO detenemos la respuesta 200 OK.
            # El error "Event loop is closed" es común aquí, el flag lo mitiga, pero no lo elimina al 100%
            logging.error(f"ERROR: Fallo al obtener/configurar el webhook durante el Health Check: {e}")
            
    return "Bot is alive (Webhooks Active)", 200

@app_flask.route('/paypal_webhook', methods=['POST'])
async def paypal_webhook_endpoint():
    """
    Endpoint dedicado a recibir notificaciones (Webhooks) de PayPal.
    """
    try:
        data = request.json
        logging.info("PayPal Webhook received.")
        await handle_paypal_webhook(data) 
        return jsonify({"status": "success", "message": "Webhook processed"}), 200
    except Exception as e:
        logging.error(f"Error processing PayPal webhook: {e}")
        return jsonify({"status": "error", "message": "Internal processing error"}), 200

@app_flask.route('/telegram_webhook', methods=['POST'])
async def telegram_webhook_endpoint():
    """
    Endpoint para recibir las actualizaciones de Telegram.
    """
    
    # === DIAGNÓSTICO 1: Webhook Recibido ===
    logging.info(">>> DIAGNÓSTICO: Webhook de Telegram recibido por Flask/Gunicorn.")
    # =======================================

    if app_tg is None:
        logging.error("Telegram Application is not initialized.")
        return jsonify({"status": "error", "message": "Bot not ready"}), 500
            
    # FIX: Asegurar que el PTB Application esté inicializado en este worker
    try:
        await app_tg.initialize()
    except Exception as e:
        logging.debug(f"PTB Application initialization check complete: {e}") 
        
    # ************************************************************
    
    # 1. Recibe el JSON de Telegram
    update_json = request.json
    # 2. Crea el objeto Update de Telegram
    update = Update.de_json(update_json, app_tg.bot)

    # === DIAGNÓSTICO 2: Update Creado y Programado ===
    if update.effective_message and update.effective_message.text:
        logging.info(f">>> DIAGNÓSTICO: Update para procesar: '{update.effective_message.text}' (Tipo: Mensaje)")
    elif update.callback_query:
        logging.info(f">>> DIAGNÓSTICO: Update para procesar: '{update.callback_query.data}' (Tipo: Callback)")
    else:
        logging.info(f">>> DIAGNÓSTICO: Update creado con éxito (Tipo: {update.effective_message.content_type if update.effective_message else 'Desconocido'})")
    # ==================================================


    # 3. Delegar el procesamiento a una tarea asíncrona usando la función auxiliar
    # CRITICAL FIX: Usamos gevent.spawn con run_async_task para evitar el RuntimeWarning 
    # de corutina no esperada y asegurar la ejecución de PTB.
    # NOTA: app_tg.process_update(update) devuelve la corutina.
    coroutine_to_run = app_tg.process_update(update)
    gevent.spawn(run_async_task, coroutine_to_run)

    # Devolver el 200 OK inmediatamente
    return jsonify({"status": "ok"}), 200

# ==========================================================
# INICIALIZACIÓN DE FIREBASE
# ==========================================================
db_initialized = False
try:
    service_account_info = json.loads(os.environ["FIREBASE_KEY"]) 
    cred = credentials.Certificate(service_account_info)
    firebase_admin.initialize_app(cred)
    db_initialized = True
    logging.info("Firebase inicializado y listo.")
except KeyError:
    logging.critical("ERROR FATAL: 'FIREBASE_KEY' no existe o está vacía. El bot no puede funcionar.")
    sys.exit(1) 
except Exception as e:
    logging.critical(f"ERROR FATAL al inicializar Firebase. Detalle: {e}")
    sys.exit(1)


# --- HANDLER DE COMANDO /buycredits ---
async def buy_credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /buycredits, redirige al menú de saldo."""
    await show_credits(update, context)

# ==========================================================
# TELEGRAM BOT INITIALIZATION FUNCTION (RUNS ON IMPORT)
# ==========================================================

def initialize_telegram_bot():
    """
    Configura y construye la aplicación PTB, asegurando que se ejecute 
    al cargar el módulo (cuando Gunicorn arranca).
    """
    global app_tg
    # 1. CONFIGURACIÓN E INICIO DEL BOT DE TELEGRAM
    app_tg = ApplicationBuilder().token(TOKEN).build() 
    
    # 2. Configuración de datos del bot (context.bot_data)
    app_tg.bot_data['MAX_FREE_CREDITS'] = MAX_FREE_CREDITS
    app_tg.bot_data['WATERMARK_TEXT'] = WATERMARK_TEXT
    app_tg.bot_data['MAX_IMAGE_SIZE_BYTES'] = MAX_IMAGE_SIZE_BYTES
    app_tg.bot_data['PAYPAL_CLIENT_ID'] = PAYPAL_CLIENT_ID
    app_tg.bot_data['PAYPAL_CLIENT_SECRET'] = PAYPAL_CLIENT_SECRET
    app_tg.bot_data['RENDER_EXTERNAL_URL'] = RENDER_URL 

    # 3. Handlers de comandos
    app_tg.add_handler(CommandHandler("start", start))
    app_tg.add_handler(CommandHandler("buycredits", buy_credits_command))
    app_tg.add_handler(CommandHandler("balance", show_credits)) 
    app_tg.add_handler(CommandHandler("help", help_command)) 
    
    # 4. Callbacks para acciones de usuario
    app_tg.add_handler(CallbackQueryHandler(show_credits, pattern="^show_credits$"))        
    app_tg.add_handler(CallbackQueryHandler(buy_credits_callback, pattern="^buy_credits_[0-9.]+$")) 
    app_tg.add_handler(CallbackQueryHandler(start, pattern="^start$"))                      
    app_tg.add_handler(CallbackQueryHandler(paypal_confirm_callback, pattern="^paypal_confirm_[0-9.]+_[0-9]+$")) 

    # 5. Callbacks para Estilos
    app_tg.add_handler(CallbackQueryHandler(dithering_colors_selected, pattern="^(8|16|32)$"))
    app_tg.add_handler(CallbackQueryHandler(style_selected, pattern="^((?![0-9.]+$).)+$"))
    
    # 6. Handlers de Mensajes
    app_tg.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, 
                                   lambda update, context: update.message.reply_text("🤔 Please use /start to choose a style or send me a photo to pixelate.")))
    
    logging.info("PTB Application Builder and Handlers configured and ready for Webhook.")

# Ejecutar la función inmediatamente.
initialize_telegram_bot()


# ==========================================================
# MAIN ARRANQUE DEL SERVIDOR (Solo para referencia de ejecución)
# ==========================================================
if __name__ == '__main__':
    logging.info("Bot reiniciado. Modo: Webhook (Ambiente Local/Test).")
    pass
