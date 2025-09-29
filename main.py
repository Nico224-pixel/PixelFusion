import logging
import os
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Final 

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore # Importado para fines de tipado y simulación

# --- Imports de Telegram ---
from telegram import Update 
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes 

# Importa tus utilidades y handlers
from handlers import start, style_selected, dithering_colors_selected, photo_handler, show_credits, buy_credits_callback, help_command, paypal_confirm_callback 
from db_utils import get_firestore_client 


# --- CONSTANTES ---
TOKEN: Final = os.environ.get("TELEGRAM_BOT_TOKEN")
MAX_FREE_CREDITS: Final = 5 
# CAMBIO A INGLÉS
WATERMARK_TEXT: Final = "FREE PIXELATION | @PixelFusionBot" 
MAX_IMAGE_SIZE_BYTES: Final = 2 * 1024 * 1024 # 2 MB
PAYPAL_CLIENT_ID: Final = os.environ.get("PAYPAL_CLIENT_ID", "SIMULATED_ID")
PAYPAL_CLIENT_SECRET: Final = os.environ.get("PAYPAL_CLIENT_SECRET", "SIMULATED_SECRET")

# ==========================================================
# FUNCIÓN DEL SERVIDOR DUMMY PARA RENDER (NECESARIO)
# ==========================================================

def run_dummy_server():
    """Inicia un servidor HTTP mínimo para que Render pueda detectar el puerto."""
    try:
        port = int(os.environ.get("PORT", 8080))
    except ValueError:
        port = 8080

    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Bot is alive (Long Polling)")

    try:
        httpd = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        print(f"*** Dummy HTTP server running on port {port} for Render Health Check. ***")
        httpd.serve_forever()
    except Exception as e:
        print(f"Error starting dummy server: {e}")

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
# MAIN ARRANQUE DEL BOT
# ==========================================================
if __name__ == '__main__':
    print("Bot reiniciado.")

    # 1. INICIA EL SERVIDOR DUMMY EN UN HILO SEPARADO
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    # 2. CONFIGURACIÓN E INICIO DEL BOT
    app = ApplicationBuilder().token(TOKEN).build()
    app.bot_data['MAX_FREE_CREDITS'] = MAX_FREE_CREDITS
    app.bot_data['WATERMARK_TEXT'] = WATERMARK_TEXT
    app.bot_data['MAX_IMAGE_SIZE_BYTES'] = MAX_IMAGE_SIZE_BYTES
    app.bot_data['PAYPAL_CLIENT_ID'] = PAYPAL_CLIENT_ID
    app.bot_data['PAYPAL_CLIENT_SECRET'] = PAYPAL_CLIENT_SECRET

    # 3. Handlers de comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buycredits", buy_credits_command))
    app.add_handler(CommandHandler("balance", show_credits)) 
    app.add_handler(CommandHandler("help", help_command)) 
    
    # 4. Callbacks para acciones de usuario
    app.add_handler(CallbackQueryHandler(show_credits, pattern="^show_credits$"))        
    app.add_handler(CallbackQueryHandler(buy_credits_callback, pattern="^buy_credits_[0-9]+$")) 
    app.add_handler(CallbackQueryHandler(start, pattern="^start$"))                      
    app.add_handler(CallbackQueryHandler(paypal_confirm_callback, pattern="^paypal_confirm_[0-9]+_[0-9]+$"))

    # 5. Callbacks para Estilos
    app.add_handler(CallbackQueryHandler(dithering_colors_selected, pattern="^(8|16|32)$"))
    app.add_handler(CallbackQueryHandler(style_selected, pattern="^(?![8|16|32]$).+"))
    
    # 6. Handlers de Mensajes
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, 
                                   lambda update, context: update.message.reply_text("🤔 Please use /start to choose a style or send me a photo to pixelate.")))


    # 7. INICIA EL LONG POLLING EN EL HILO PRINCIPAL
    print("*** Starting Telegram Bot Long Polling... ***")
    app.run_polling()