import logging
import os
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# --- Imports de Telegram ---
from telegram import Update 
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes 

# Importa tus utilidades y handlers
from handlers import start, style_selected, dithering_colors_selected, photo_handler
from db_utils import get_firestore_client 
from PIL import Image

# --- CONSTANTES ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MAX_FREE_CREDITS = 10 
WATERMARK_TEXT = "PIXELADO GRATIS | @PixelFusionBot"
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024 # 5 MB

# ==========================================================
# FUNCIÓN DEL SERVIDOR DUMMY PARA RENDER (¡LA CLAVE!)
# ==========================================================

def run_dummy_server():
    """
    Inicia un servidor HTTP mínimo en un hilo separado para que Render
    pueda detectar un puerto abierto y mantener el Web Service activo.
    """
    try:
        # Render asigna el puerto a través de la variable de entorno PORT
        # Es crucial usar 'PORT' aquí
        port = int(os.environ.get("PORT", 8080))
    except ValueError:
        port = 8080

    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Responde con un simple 200 OK para el chequeo de Render
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Bot is alive (Long Polling)")

    try:
        httpd = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        print(f"*** Dummy HTTP server running on port {port} for Render Health Check. ***")
        # El servidor se inicia en el hilo actual, que será el hilo separado por 'threading'
        httpd.serve_forever()
    except Exception as e:
        print(f"Error starting dummy server: {e}")


# ==========================================================
# INICIALIZACIÓN DE FIREBASE (Mantenido)
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


# --- HANDLER DE PRUEBA: Recargar créditos (Mantenido) ---
async def buy_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (Tu lógica de buy_credits se mantiene igual)
    db = get_firestore_client()
    user_id = update.message.from_user.id

    if db is None:
        await update.message.reply_text("❌ La base de datos no está disponible. No se puede recargar.")
        return

    # SIMULACIÓN: Añadir 5 créditos
    CREDITS_TO_ADD = 5
    user_ref = db.collection('users').document(str(user_id))

    try:
        user_ref.update({'paid_credits': firestore.Increment(CREDITS_TO_ADD)})
        await update.message.reply_text(
            f"✅ ¡Compra simulada exitosa! Se han añadido **{CREDITS_TO_ADD}** créditos a tu cuenta.\n"
            "Úsalos para generar imágenes sin marca de agua.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error al simular la recarga de créditos: {e}")
        await update.message.reply_text("❌ Error al actualizar tu saldo. Intenta de nuevo.")

# ==========================================================
# MAIN ARRANQUE DEL BOT (Modificado)
# ==========================================================
if __name__ == '__main__':
    print("Bot reiniciado.")

    # 1. INICIA EL SERVIDOR DUMMY EN UN HILO SEPARADO
    # Esto debe hacerse ANTES de app.run_polling()
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    # 2. CONFIGURACIÓN E INICIO DEL BOT
    app = ApplicationBuilder().token(TOKEN).build()
    app.bot_data['MAX_FREE_CREDITS'] = MAX_FREE_CREDITS
    app.bot_data['WATERMARK_TEXT'] = WATERMARK_TEXT
    app.bot_data['MAX_IMAGE_SIZE_BYTES'] = MAX_IMAGE_SIZE_BYTES

    # 3. Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buycredits", buy_credits))
    app.add_handler(CallbackQueryHandler(dithering_colors_selected, pattern="^(8|16|32)$"))
    app.add_handler(CallbackQueryHandler(style_selected, pattern="^(?![8|16|32]$).+"))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    # 4. INICIA EL LONG POLLING EN EL HILO PRINCIPAL
    print("*** Starting Telegram Bot Long Polling... ***")
    app.run_polling()