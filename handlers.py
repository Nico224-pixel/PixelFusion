# handlers.py

import logging
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, filters
from db_utils import get_user_data, decrement_credit, record_image_usage # Importamos record_image_usage
from image_processing import pixelate_image, apply_watermark, STYLE_DEFAULTS
from PIL import Image

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra los botones de estilo."""
    keyboard = [[InlineKeyboardButton(name, callback_data=name)] for name in STYLE_DEFAULTS.keys()]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🎮 Selecciona un estilo de pixel art:", reply_markup=reply_markup)

# --- Callbacks ---
async def style_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la selección inicial del estilo."""
    query = update.callback_query
    await query.answer()
    style = query.data
    context.user_data["style"] = style

    if style != "dithering":
        context.user_data.pop("dithering_colors", None)

    if style == "dithering":
        keyboard = [
             [InlineKeyboardButton("8", callback_data="8")],
             [InlineKeyboardButton("16", callback_data="16")],
             [InlineKeyboardButton("32", callback_data="32")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("🎨 Selecciona la cantidad de colores para dithering:", reply_markup=reply_markup)
    else:
        await query.edit_message_text(f"Has seleccionado el estilo *{style}*.\nAhora envía la foto 📸", parse_mode="Markdown")

# ¡ESTA PARTE DEBE EXISTIR EN handlers.py!
async def dithering_colors_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la selección de colores para dithering."""
    query = update.callback_query
    await query.answer()
    colors = int(query.data)

    context.user_data["style"] = "dithering"
    context.user_data["dithering_colors"] = colors

    style = context.user_data["style"]
    await query.edit_message_text(f"🎨 Dithering seleccionado con {colors} colores.\nAhora envía la foto 📸 para aplicar el estilo *{style}*.", parse_mode="Markdown")

# --- Handler para FOTOS CON LÓGICA DE CRÉDITOS ---
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if "style" not in context.user_data:
        await msg.reply_text("Primero selecciona un estilo con /start.")
        return

    # Inyección de constantes faltantes, obtenidas de main.py/bot_data
    MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10) # Default si falla
    WATERMARK_TEXT = context.application.bot_data.get('WATERMARK_TEXT', "WM")
    MAX_SIZE = context.application.bot_data.get('MAX_IMAGE_SIZE_BYTES', 5242880) # Default 5MB

    # 1. VERIFICACIÓN DE CRÉDITOS Y LÍMITES
    user_id = msg.from_user.id

    user_data = get_user_data(user_id, MAX_FREE_CREDITS)

    # AHORA USAMOS total_credits
    # total_credits_remaining es la suma de paid_credits y free_credits del momento
    total_credits_remaining = user_data.get('total_credits', 0) 

    # Extraemos el saldo individual para el mensaje final (estos son los saldos ANTES del descuento)
    free_credits = user_data.get('free_credits', 0)
    paid_credits = user_data.get('paid_credits', 0)

    apply_wm = False 

    # Decidimos si se descuenta el crédito y si se aplica marca de agua
    if total_credits_remaining > 0:
        # El usuario tiene créditos: Descontamos uno 

        # Llama a la nueva función atómica que decide si gasta de free o paid
        decrement_credit(user_id) 
        total_credits_remaining -= 1 

        # Actualizamos localmente el saldo individual para el caption DESPUÉS del descuento
        # Asumimos que decrement_credit gastó de free primero
        if free_credits > 0:
            free_credits -= 1
        elif paid_credits > 0:
            paid_credits -= 1

    else:
        # El usuario NO tiene créditos: APLICAMOS marca de agua
        apply_wm = True
        await msg.reply_text(
            f"😔 **¡Créditos agotados!** Tu imagen se procesará, pero se le añadirá una **marca de agua**."
            "\n\n✨ Usa /buycredits para obtener imágenes sin marca y apoyar el bot.",
            parse_mode="Markdown"
        )
    # ----------------------------------------------------

    # 2. VERIFICACIÓN DE TAMAÑO DEL ARCHIVO
    file_info = msg.photo[-1]
    file_size = file_info.file_size

    if file_size > MAX_SIZE:
        await msg.reply_text(
            f"❌ **¡Imagen muy grande!** El tamaño máximo permitido es de "
            f"{MAX_SIZE / (1024 * 1024):.1f} MB. "
            "Por favor, intenta con otra foto más pequeña."
        )
        return
    # ----------------------------------------------------

    # 3. DESCARGA Y PROCESAMIENTO
    photo_file = await msg.photo[-1].get_file()
    photo_bytes = BytesIO()

    try:
        await photo_file.download_to_memory(out=photo_bytes)
    except Exception as e:
        logging.error(f"Error al descargar la foto: {e}")
        await msg.reply_text("❌ No pude descargar la foto. Intenta de nuevo.")
        return

    photo_bytes.seek(0)
    try:
        img = Image.open(photo_bytes).convert("RGB")
    except Exception as e:
        logging.error(f"Error al abrir la imagen: {e}")
        await msg.reply_text("❌ El archivo no parece ser una imagen válida.")
        return

    style = context.user_data.get("style", "nes")
    dithering_colors = context.user_data.get("dithering_colors")

    await msg.reply_text("⚙️ Procesando imagen, espera un momento...")

    try:
        pixel_img = pixelate_image(img, style=style, dithering_colors=dithering_colors)
    except Exception as e:
        logging.error(f"Error al procesar la imagen: {e}")
        await msg.reply_text(f"❌ Ocurrió un error al aplicar el estilo. Detalle: {e}")
        return

    if apply_wm:
        pixel_img = apply_watermark(pixel_img, WATERMARK_TEXT)

    out_bytes = BytesIO()
    pixel_img.save(out_bytes, format="PNG")
    out_bytes.seek(0)

    caption = f"✅ Estilo aplicado: {style.replace('dithering', 'Dithering')}"
    if style == "dithering" and dithering_colors:
        caption += f" ({dithering_colors} colores)"

    # Mensaje de saldo DETALLADO - ¡CORREGIDO!
    caption += f"\n\n💰 Saldo restante: **{total_credits_remaining}** créditos"
    caption += f"\n(Gratuitos: {free_credits}, Comprados: {paid_credits})"

    await msg.reply_photo(photo=out_bytes, caption=caption, parse_mode="Markdown")

    # 5. REGISTRAR USO DESPUÉS DEL PROCESAMIENTO EXITOSO
    record_image_usage(user_id=user_id, style=style, is_watermarked=apply_wm)

    context.user_data.pop("style", None)
    context.user_data.pop("dithering_colors", None)