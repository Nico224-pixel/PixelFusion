import logging
import os
import json
import threading
from typing import Final 

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore 

# --- FLASK Imports ---
from flask import Flask, request, jsonify 

# --- Imports de Telegram ---
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

# ==========================================================
# INICIALIZACIÓN DE FLASK (Servidor Webhook)
# ==========================================================

app_flask = Flask(__name__)

@app_flask.route('/', methods=['GET'])
def health_check_endpoint():
    """Endpoint de Health Check para Render."""
    return "Bot is alive (Long Polling & Webhooks Active)", 200

@app_flask.route('/paypal_webhook', methods=['POST'])
def paypal_webhook_endpoint():
    """
    Endpoint dedicado a recibir notificaciones (Webhooks) de PayPal.
    """
    try:
        data = request.json
        logging.info("PayPal Webhook received.")
        
        # Llama al handler real de PayPal para acreditar los créditos
        handle_paypal_webhook(data) 
        
        # Siempre debe retornar 200 para evitar que PayPal reintente.
        return jsonify({"status": "success", "message": "Webhook processed"}), 200

    except Exception as e:
        logging.error(f"Error processing PayPal webhook: {e}")
        return jsonify({"status": "error", "message": "Internal processing error"}), 200
    
# ==========================================================
# FUNCIÓN DE ARRANQUE DEL BOT (en hilo)
# ==========================================================
def run_telegram_bot(app_tg):
    """
    Inicia el bot de Telegram en modo Long Polling. 
    Usar run_polling() dentro de un hilo mitiga el error 'set_wakeup_fd'.
    """
    print("*** Starting Telegram Bot Long Polling... ***")
    try:
        app_tg.run_polling()
    except Exception as e:
        logging.critical(f"Telegram Bot Polling failed: {e}")

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
# MAIN ARRANQUE DEL BOT Y SERVIDOR
# ==========================================================
if __name__ == '__main__':
    print("Bot reiniciado.")
    
    # 1. CONFIGURACIÓN E INICIO DEL BOT DE TELEGRAM
    app_tg = ApplicationBuilder().token(TOKEN).build() 
    app_tg.bot_data['MAX_FREE_CREDITS'] = MAX_FREE_CREDITS
    app_tg.bot_data['WATERMARK_TEXT'] = WATERMARK_TEXT
    app_tg.bot_data['MAX_IMAGE_SIZE_BYTES'] = MAX_IMAGE_SIZE_BYTES
    app_tg.bot_data['PAYPAL_CLIENT_ID'] = PAYPAL_CLIENT_ID
    app_tg.bot_data['PAYPAL_CLIENT_SECRET'] = PAYPAL_CLIENT_SECRET

    # 2. Handlers de comandos
    app_tg.add_handler(CommandHandler("start", start))
    app_tg.add_handler(CommandHandler("buycredits", buy_credits_command))
    app_tg.add_handler(CommandHandler("balance", show_credits)) 
    app_tg.add_handler(CommandHandler("help", help_command)) 
    
    # 3. Callbacks para acciones de usuario
    app_tg.add_handler(CallbackQueryHandler(show_credits, pattern="^show_credits$"))        
    app_tg.add_handler(CallbackQueryHandler(buy_credits_callback, pattern="^buy_credits_[0-9.]+$")) 
    app_tg.add_handler(CallbackQueryHandler(start, pattern="^start$"))                      
    app_tg.add_handler(CallbackQueryHandler(paypal_confirm_callback, pattern="^paypal_confirm_[0-9.]+_[0-9]+$"))

    # 4. Callbacks para Estilos
    app_tg.add_handler(CallbackQueryHandler(dithering_colors_selected, pattern="^(8|16|32)$"))
    app_tg.add_handler(CallbackQueryHandler(style_selected, pattern="^((?![0-9.]+$).)+$"))
    
    # 5. Handlers de Mensajes
    app_tg.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, 
                                   lambda update, context: update.message.reply_text("🤔 Please use /start to choose a style or send me a photo to pixelate.")))
    
    # 6. INICIA EL BOT DE TELEGRAM EN UN HILO SEPARADO
    telegram_thread = threading.Thread(target=run_telegram_bot, args=(app_tg,), daemon=True)
    telegram_thread.start()

    # 7. INICIA EL SERVIDOR WEB DE FLASK (en el hilo principal, escucha el puerto)
    port = int(os.environ.get("PORT", 8080))
    print(f"*** Flask Webhook Server running on port {port} at http://0.0.0.0:{port} ***")
    app_flask.run(host='0.0.0.0', port=port, debug=False)