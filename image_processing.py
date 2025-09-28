# image_processing.py

import logging
from io import BytesIO
from PIL import Image, ImageFilter, ImageOps, ImageDraw, ImageFont
import math
from typing import Dict, Any

# --- Estilos disponibles (Constante) ---
STYLE_DEFAULTS: Dict[str, Dict[str, int]] = {
    "atari":           {"base_pixel": 16, "n_colors": 8},
    "nes":             {"base_pixel": 12, "n_colors": 8},
    "snes":            {"base_pixel": 8,  "n_colors": 32},
    "gameboy":         {"base_pixel": 10, "n_colors": 4},
    "gameboycolor":    {"base_pixel": 10, "n_colors": 56},
    "genesis":         {"base_pixel": 8,  "n_colors": 64},
    "ps1":             {"base_pixel": 4,  "n_colors": 256},
    "commodore64":     {"base_pixel": 12, "n_colors": 16},
    "amiga":           {"base_pixel": 8,  "n_colors": 32},
    "xbox":            {"base_pixel": 2,  "n_colors": 256},
    "dithering":       {"base_pixel": 1, "n_colors": 16},
}

# --- FUNCIÓN INSTRUMENTADA: pixelate_image ---
def pixelate_image(img, style="nes", pixel_size=None, dithering_colors=None, blur=False):
    # ... (Tu código de pixelate_image va aquí, no necesita cambios)

    # Logs para diagnóstico final
    # ... (cuerpo de la función pixelate_image tal cual lo tenías)

    defaults = STYLE_DEFAULTS.get(style.lower(), STYLE_DEFAULTS["nes"])
    n_colors = dithering_colors if style.lower() == "dithering" and dithering_colors else defaults["n_colors"]
    pixel_size = defaults["base_pixel"] if pixel_size is None else defaults["base_pixel"]

    if style.lower() == "dithering":
        pixel_art = img.convert("RGB").quantize(
            colors=n_colors,
            dither=Image.Dither.FLOYDSTEINBERG
        ).convert("RGB")
    else:
        w, h = img.size
        small_w = max(1, w // pixel_size)
        small_h = max(1, h // pixel_size)

        small = img.filter(ImageFilter.GaussianBlur(radius=1)) if blur else img
        small = small.resize((small_w, small_h), resample=Image.Resampling.BILINEAR)
        small = small.quantize(colors=n_colors, method=Image.Quantize.MEDIANCUT).convert("RGB")
        pixel_art = small.resize((w, h), resample=Image.Resampling.NEAREST)

    return pixel_art


# --- FUNCIÓN DE MARCA DE AGUA: CENTRADA, GRANDE Y ÚNICA ---
def apply_watermark(img: Image.Image, watermark_text: str) -> Image.Image:
    """Aplica un mensaje grande y centrado con opacidad, sin cuadro de fondo."""
    try:
        if img.mode != 'RGBA':
            img = img.convert("RGBA") 

        w, h = img.size

        # Configuración de Fuente y Opacidad
        font_size = max(150, int(w / 10)) # Tamaño de fuente grande

        alpha = 150 
        fill_color = (255, 255, 255, alpha) 

        # Manejo de Fuentes
        try:
            font = ImageFont.truetype("arial.ttf", font_size) 
        except IOError:
            font = ImageFont.load_default()

        # Crear la Capa de Marca de Agua (Overlay)
        watermark_overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(watermark_overlay)

        # Calcular Posición Central
        bbox = overlay_draw.textbbox((0, 0), watermark_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        x_pos = (w - text_w) // 2
        y_pos = (h - text_h) // 2

        # Dibujar el Texto
        overlay_draw.text((x_pos, y_pos), watermark_text, font=font, fill=fill_color)

        # Rotar la Capa del Texto
        rotated_overlay = watermark_overlay.rotate(15, expand=False, resample=Image.Resampling.BICUBIC) 

        # Fusionar la Capa Rotada con la Imagen Base
        img = Image.alpha_composite(img, rotated_overlay)

    except Exception as e:
        logging.error(f"Error crítico al aplicar la marca de agua centrada: {e}")
        return img.convert("RGB") 

    return img.convert("RGB")