# handlers.py (REESCRITO)

import logging
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db_utils import get_user_data, decrement_credit, record_image_usage # Importamos record_image_usage
from image_processing import pixelate_image, apply_watermark, STYLE_DEFAULTS
from PIL import Image

# --- Ayuda de Interfaz ---
def get_style_keyboard():
    """Genera el teclado para la selección de estilos."""
    keyboard = [[InlineKeyboardButton(name.upper(), callback_data=name)] for name in STYLE_DEFAULTS.keys()]
    
    # Botón de CTA (Llamada a la Acción)
    keyboard.append([InlineKeyboardButton("💰 Consultar saldo /buycredits", url="https://t.me/PixelFusionBot?start=credits")]) # Reemplaza con tu bot_username
    
    return InlineKeyboardMarkup(keyboard)


# ==========================================================
# 1. COMANDOS PRINCIPALES Y CALLBACKS
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el saludo, el saldo actual y los botones de estilo."""
    user_id = update.message.from_user.id
    MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10)
    user_data = get_user_data(user_id, MAX_FREE_CREDITS)
    
    saldo_msg = f"✨ ¡Hola {update.message.from_user.first_name}! Soy el Bot de Pixel Art.\n\n"
    saldo_msg += f"**💰 Saldo:** {user_data.get('total_credits', 0)} créditos (Gratuitos: {user_data.get('free_credits', 0)})"
    saldo_msg += "\n\n**1.** Selecciona un estilo abajo. **2.** Envía tu foto 📸"
    
    await update.message.reply_text(saldo_msg, reply_markup=get_style_keyboard(), parse_mode="Markdown")


async def show_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el saldo detallado del usuario (Nuevo comando /saldo)."""
    user_id = update.message.from_user.id
    MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10)
    user_data = get_user_data(user_id, MAX_FREE_CREDITS)

    saldo_msg = (
        f"💳 **Estado de tu Cuenta**\n\n"
        f"   - **Créditos Gratuitos:** **{user_data.get('free_credits', 0)}** (Se recargan semanalmente hasta {MAX_FREE_CREDITS})\n"
        f"   - **Créditos Comprados:** **{user_data.get('paid_credits', 0)}** (Imágenes sin marca de agua)\n"
        f"   - **TOTAL:** **{user_data.get('total_credits', 0)}** créditos.\n\n"
        "Usa /buycredits para recargar y eliminar la marca de agua."
    )
    await update.message.reply_text(saldo_msg, parse_mode="Markdown")


async def style_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la selección inicial del estilo."""
    query = update.callback_query
    await query.answer()
    style = query.data
    context.user_data["style"] = style

    if style != "dithering":
        # Flujo para estilos fijos (NES, SNES, etc.)
        context.user_data.pop("dithering_colors", None)
        await query.edit_message_text(f"✅ Estilo **{style.upper()}** seleccionado.\n\nAhora, **¡envía la foto 📸!**", parse_mode="Markdown")

    else:
        # Flujo para Dithering (requiere selección de colores)
        context.user_data.pop("dithering_colors", None)
        keyboard = [
             [InlineKeyboardButton("8 Colores", callback_data="8")],
             [InlineKeyboardButton("16 Colores", callback_data="16")],
             [InlineKeyboardButton("32 Colores", callback_data="32")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("🎨 **Dithering** seleccionado. ¿Cuántos colores quieres usar?", reply_markup=reply_markup)


async def dithering_colors_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la selección de colores para dithering."""
    query = update.callback_query
    await query.answer()
    colors = int(query.data)

    context.user_data["style"] = "dithering"
    context.user_data["dithering_colors"] = colors

    style = context.user_data["style"]
    await query.edit_message_text(f"✅ **Dithering con {colors} colores** listo.\n\nAhora, **¡envía la foto 📸!**", parse_mode="Markdown")


# ==========================================================
# 2. HANDLER PRINCIPAL DE FOTOS
# ==========================================================

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    # 1. VERIFICACIÓN: ¿Se seleccionó un estilo?
    if "style" not in context.user_data:
        await msg.reply_text("🤔 Por favor, selecciona un estilo primero:", reply_markup=get_style_keyboard())
        return

    # Inyección de constantes del bot_data
    MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10) 
    WATERMARK_TEXT = context.application.bot_data.get('WATERMARK_TEXT', "WM")
    MAX_SIZE = context.application.bot_data.get('MAX_IMAGE_SIZE_BYTES', 5242880) 

    user_id = msg.from_user.id
    style = context.user_data["style"]
    dithering_colors = context.user_data.get("dithering_colors")
    
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
    
    # 3. VERIFICACIÓN Y DESCUENTO DE CRÉDITOS
    user_data = get_user_data(user_id, MAX_FREE_CREDITS)
    total_credits_before = user_data.get('total_credits', 0) 
    
    apply_wm = False 
    
    if total_credits_before > 0:
        # El usuario tiene saldo: Descontar crédito atómicamente
        decrement_credit(user_id) 
        
        # Recuperar el saldo ACTUALIZADO después del descuento para los mensajes
        user_data_after = get_user_data(user_id, MAX_FREE_CREDITS)
        free_credits = user_data_after.get('free_credits', 0)
        paid_credits = user_data_after.get('paid_credits', 0)
        total_credits_remaining = user_data_after.get('total_credits', 0)

    else:
        # El usuario NO tiene créditos: Aplicar marca de agua
        apply_wm = True
        free_credits = user_data.get('free_credits', 0)
        paid_credits = user_data.get('paid_credits', 0)
        total_credits_remaining = 0

        # Mensaje de advertencia de UX mejorado
        await msg.reply_text(
            f"😔 **¡Créditos agotados!** Tu imagen se procesará, pero se le añadirá una **marca de agua**."
            f"\n\n✨ Tienes {free_credits + paid_credits} créditos totales. Usa /buycredits para recargar y evitar la marca de agua.",
            parse_mode="Markdown"
        )
    # ----------------------------------------------------

    # 4. FEEDBACK DE PROCESAMIENTO (UX)
    await context.bot.send_chat_action(chat_id=msg.chat_id, action="upload_photo")
    await msg.reply_text("⚙️ **¡Foto recibida!** Procesando imagen, espera un momento...", parse_mode="Markdown")

    # 5. DESCARGA Y PROCESAMIENTO (Lógica mantenida)
    photo_file = await file_info.get_file()
    photo_bytes = BytesIO()

    try:
        await photo_file.download_to_memory(out=photo_bytes)
        photo_bytes.seek(0)
        img = Image.open(photo_bytes).convert("RGB")
    except Exception as e:
        logging.error(f"Error al descargar/abrir la foto: {e}")
        await msg.reply_text("❌ No pude descargar o abrir la foto. Intenta de nuevo.")
        return

    try:
        pixel_img = pixelate_image(img, style=style, dithering_colors=dithering_colors)
    except Exception as e:
        logging.error(f"Error al procesar la imagen: {e}")
        await msg.reply_text("❌ Ocurrió un error al aplicar el estilo. Intenta de nuevo.")
        return

    if apply_wm:
        pixel_img = apply_watermark(pixel_img, WATERMARK_TEXT)

    out_bytes = BytesIO()
    pixel_img.save(out_bytes, format="PNG")
    out_bytes.seek(0)

    # 6. ENVIAR RESULTADO Y CAPTION
    
    caption = f"✅ **Estilo aplicado:** {style.upper()}"
    if style == "dithering" and dithering_colors:
        caption += f" ({dithering_colors} colores)"

    # Mensaje de saldo DETALLADO después del uso
    if not apply_wm:
        caption += f"\n\n💰 Te queda un saldo de **{total_credits_remaining}** créditos.\n(Gratuitos: {free_credits}, Comprados: {paid_credits})"
    else:
         caption += "\n\n✨ Generada con marca de agua. ¡Recarga con /buycredits para quitársela!"


    await msg.reply_photo(photo=out_bytes, 
                          caption=caption, 
                          parse_mode="Markdown",
                          reply_markup=InlineKeyboardMarkup([
                              [InlineKeyboardButton("🎨 Cambiar Estilo /start", callback_data="ignore")]
                          ])
                          )

    # 7. REGISTRAR USO
    record_image_usage(user_id=user_id, style=style, is_watermarked=apply_wm)

    # 8. Limpiar user_data
    context.user_data.pop("style", None)
    context.user_data.pop("dithering_colors", None)