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
# Asegúrate de que esto se ejecute lo antes posible.
monkey.patch_all(subprocess=False) 
# --------------------------------------------------------

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore 

# --- FLASK Imports ---
from flask import Flask, request, jsonify 

# --- Imports de Telegram ---
import telegram # Import telegram for error handling
from telegram import Update 
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes 

# Importa tus utilidades y handlers
from handlers import start, style_selected, dithering_colors_selected, photo_handler, show_credits, buy_credits_callback, help_command, paypal_confirm_callback, handle_paypal_webhook 
from db_utils import get_firestore_client 
import paypal_utils 


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
# Nueva bandera para la comprobación de estado de webhook
webhook_status_checked = False 


@app_flask.route('/', methods=['GET'])
async def health_check_endpoint(): # <--- AHORA ES ASÍNCRONO
    """
    Endpoint de Health Check para Render.
    También se usa para chequear y configurar el webhook si es necesario.
    """
    global webhook_status_checked
    
    if app_tg is None:
        return "Bot not ready (TG app is None)", 500

    # Esta lógica solo se ejecuta la primera vez que un worker recibe el Health Check
    if not webhook_status_checked:
        try:
            await app_tg.initialize()
            webhook_info = await app_tg.bot.get_webhook_info()
            webhook_url = f"{RENDER_URL}/telegram_webhook"
            
            # 1. Comprobar si la URL actual es la correcta
            if webhook_info.url != webhook_url:
                logging.warning(f"Webhook INCORRECTO: Telegram tiene '{webhook_info.url}'. Intentando corregir a '{webhook_url}'.")
                
                # Intentar corregir/configurar el webhook
                await app_tg.bot.delete_webhook()
                await app_tg.bot.set_webhook(url=webhook_url)
                logging.info(f"*** Webhook CORREGIDO y configurado en: {webhook_url} ***")
            else:
                logging.info(f"Webhook OK: La URL actual registrada en Telegram es correcta: {webhook_info.url}")
            
            # Registrar que la comprobación inicial se realizó con éxito
            webhook_status_checked = True 

        except Exception as e:
            logging.error(f"ERROR: Fallo al obtener/configurar el webhook durante el Health Check: {e}")
            
    return "Bot is alive (Webhooks Active)", 200

@app_flask.route('/paypal_webhook', methods=['POST'])
async def paypal_webhook_endpoint(): # <--- DEBE SER ASÍNCRONO
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


    # 3. Delegar el procesamiento a una tarea asíncrona
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    loop.create_task(app_tg.process_update(update))

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
