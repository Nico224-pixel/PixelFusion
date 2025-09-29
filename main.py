import logging
import os
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Final 

import firebase_admin
from firebase_admin import credentials
# Importamos firestore solo para fines de tipado y simulación en buy_credits_command
from firebase_admin import firestore

# --- Imports de Telegram ---
from telegram import Update 
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes 

# Importa tus utilidades y handlers
from handlers import start, style_selected, dithering_colors_selected, photo_handler, show_credits, buy_credits_callback 
from db_utils import get_firestore_client 
from PIL import Image

# --- CONSTANTES ---
TOKEN: Final = os.environ.get("TELEGRAM_BOT_TOKEN")
MAX_FREE_CREDITS: Final = 10 
WATERMARK_TEXT: Final = "PIXELADO GRATIS | @PixelFusionBot"
# Límite de imagen en 2 MB
MAX_IMAGE_SIZE_BYTES: Final = 2 * 1024 * 1024 
CREDITS_TO_ADD: Final = 5 # Créditos fijos para la simulación de compra

# ==========================================================
# FUNCIÓN DEL SERVIDOR DUMMY PARA RENDER (NECESARIO)
# ==========================================================

def run_dummy_server():
    """Inicia un servidor HTTP mínimo en un hilo separado para que Render pueda detectar el puerto."""
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

# --- HANDLER DE PRUEBA: Recargar créditos (Llamada al menú de Saldo) ---
async def buy_credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el comando /buycredits, redirige al menú de saldo."""
    # buy_credits_command y /saldo hacen lo mismo: mostrar el menú de créditos
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
    app.bot_data['CREDITS_TO_ADD'] = CREDITS_TO_ADD # Para la simulación de compra

    # 3. Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buycredits", buy_credits_command))
    app.add_handler(CommandHandler("saldo", show_credits)) 
    
    # 4. Callbacks para acciones de usuario
    app.add_handler(CallbackQueryHandler(show_credits, pattern="^show_credits$"))        # Botón para ir al menú de saldo
    app.add_handler(CallbackQueryHandler(buy_credits_callback, pattern="^buy_credits_sim$")) # Botón para simular compra
    app.add_handler(CallbackQueryHandler(start, pattern="^start$"))                      # Botón para volver al inicio

    # 5. Callbacks para Estilos
    app.add_handler(CallbackQueryHandler(dithering_colors_selected, pattern="^(8|16|32)$"))
    app.add_handler(CallbackQueryHandler(style_selected, pattern="^(?![8|16|32]$).+"))
    
    # 6. Handlers de Mensajes
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    # Maneja texto/stickers inesperados (Mejora de UX)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, 
                                   lambda update, context: update.message.reply_text("🤔 Por favor, usa /start para elegir un estilo o envíame una foto para pixelar.")))


    # 7. INICIA EL LONG POLLING EN EL HILO PRINCIPAL
    print("*** Starting Telegram Bot Long Polling... ***")
    app.run_polling()