import logging
import os
import json
from typing import Final 
import asyncio

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore 

# --- FLASK Imports ---
from flask import Flask, request, jsonify 

# --- Imports de Telegram ---
from telegram import Update 
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes 

# Importa tus utilidades y handlers
# Asegúrate de que handle_paypal_webhook esté importado aquí
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

# ==========================================================
# INICIALIZACIÓN DE FLASK (Servidor Webhook)
# ==========================================================

app_flask = Flask(__name__)
# Variables globales para la app de Telegram y el estado de inicialización
app_tg = None 
bot_initialized_on_webhook = False 

@app_flask.route('/', methods=['GET'])
def health_check_endpoint():
    """Endpoint de Health Check para Render."""
    return "Bot is alive (Webhooks Active)", 200

@app_flask.route('/paypal_webhook', methods=['POST'])
async def paypal_webhook_endpoint(): # <--- DEBE SER ASÍNCRONO
    """
    Endpoint dedicado a recibir notificaciones (Webhooks) de PayPal.
    NOTA: En producción, aquí se debe verificar la firma del webhook.
    """
    try:
        data = request.json
        logging.info("PayPal Webhook received.")
        # Llamar a la función asíncrona para procesar el webhook
        await handle_paypal_webhook(data) 
        return jsonify({"status": "success", "message": "Webhook processed"}), 200
    except Exception as e:
        logging.error(f"Error processing PayPal webhook: {e}")
        return jsonify({"status": "error", "message": "Internal processing error"}), 200

@app_flask.route('/telegram_webhook', methods=['POST'])
async def telegram_webhook_endpoint():
    """
    Endpoint para recibir las actualizaciones de Telegram.
    Realiza la configuración del webhook si es la primera vez.
    """
    global bot_initialized_on_webhook
    
    # app_tg ya está inicializado (gracias a initialize_telegram_bot()), solo verificamos el token
    if app_tg is None:
        logging.error("Telegram Application is not initialized (Check Gunicorn setup).")
        return jsonify({"status": "error", "message": "Bot not ready"}), 500

    # *** PASO CRÍTICO: Configuración de Webhook Condicional ***
    if not bot_initialized_on_webhook:
        # Esto se ejecuta solo la primera vez que Telegram envía un mensaje.
        webhook_url = f"{RENDER_URL}/telegram_webhook"
        try:
            # NOTE: app_tg is already initialized, we just need to set the webhook URL.
            await app_tg.initialize() # Llama a initialize() para preparar el bot
            await app_tg.bot.delete_webhook() 
            await app_tg.bot.set_webhook(url=webhook_url)
            print(f"*** Webhook de Telegram configurado en: {webhook_url} ***")
            bot_initialized_on_webhook = True
        except Exception as e:
            logging.error(f"FATAL: Falló la configuración de webhook de PTB: {e}")
            return jsonify({"status": "error", "message": "PTB Webhook Setup Failed"}), 500
    # ************************************************************
    
    # 1. Recibe el JSON de Telegram
    update_json = request.json
    # 2. Crea el objeto Update de Telegram
    update = Update.de_json(update_json, app_tg.bot)

    # 3. Procesa el update asíncronamente
    await app_tg.process_update(update)

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
    print("Firebase inicializado y listo.")
except KeyError:
    print("ERROR FATAL: 'FIREBASE_KEY' no existe o está vacía.")
except Exception as e:
    print(f"ERROR FATAL al inicializar Firebase. Detalle: {e}")
finally:
    if not db_initialized:
        print("El bot funcionará sin lógica de créditos.")

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
    # El callback de confirmación se mantiene por si acaso
    app_tg.add_handler(CallbackQueryHandler(paypal_confirm_callback, pattern="^paypal_confirm_[0-9.]+_[0-9]+$")) 

    # 5. Callbacks para Estilos
    app_tg.add_handler(CallbackQueryHandler(dithering_colors_selected, pattern="^(8|16|32)$"))
    app_tg.add_handler(CallbackQueryHandler(style_selected, pattern="^((?![0-9.]+$).)+$"))
    
    # 6. Handlers de Mensajes
    app_tg.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, 
                                   lambda update, context: update.message.reply_text("🤔 Please use /start to choose a style or send me a photo to pixelate.")))
    
    print("PTB Application Builder and Handlers configured and ready for Webhook.")

# Ejecutar la función inmediatamente. Esto garantiza que app_tg esté disponible 
# tan pronto como el módulo main.py sea importado por Gunicorn.
initialize_telegram_bot()


# ==========================================================
# MAIN ARRANQUE DEL SERVIDOR (Solo para referencia de ejecución)
# ==========================================================
if __name__ == '__main__':
    print("Bot reiniciado. Modo: Webhook (Ambiente Local/Test).")
    # El comando de arranque de Gunicorn en Render ignora este bloque, 
    # pero el resto del código ya ha definido las funciones de Flask y PTB.
    pass