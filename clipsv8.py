import os
import re
import sys
import json
import time
import shutil
import argparse
import itertools
import subprocess
import tempfile
import wave
import math
import statistics
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from tkinter import Tk, filedialog
from typing import Optional, List, Dict, Any, Tuple


# ================== RUTAS DE ASSETS ==================

SCRIPT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()

def resolver_asset(*nombres):
    """Busca assets primero en la carpeta de ejecución y luego junto al script.
    Acepta nombres con y sin .png para evitar problemas con extensiones ocultas en Windows.
    """
    candidatos = []
    for nombre in nombres:
        if not nombre:
            continue
        candidatos.append(Path.cwd() / nombre)
        candidatos.append(SCRIPT_DIR / nombre)
    for candidato in candidatos:
        if candidato.exists():
            return str(candidato)
    # Si no existe aún, devuelve el primer nombre relativo para que el mensaje de error sea claro.
    return str(Path.cwd() / nombres[0])


# ===================================================
# CREADOR DE REELS - Línea 90Min layout vertical v7.4 ESTABLE
#
# Incluye:
# - Layout vertical 1080x1920.
# - Intro corta tipo redes + hook inicial.
# - Marcador arriba calibrable con mouse.
# - Recorte del marcador guardado en JSON.
# - Fallback automático al marcador fijo antiguo.
# - Detección robusta de audio.
# - Silencio automático si el video no tiene audio.
# - Detección de FPS, duración, códecs y resolución.
# - Presets de calidad: fast / balanced / high.
# - Encoder inteligente: prueba real de GPU y cae seguro a libx264.
# - Modo plan-only.
# - Barra de progreso visual, cierre Twitch y audio normalizado.
# ===================================================


# ================== CONFIGURACIÓN ==================

# Nombres esperados según tu carpeta de trabajo:
#   logo_canal.png, logo_twitch.png, plantilla_intro.png, plantilla_outro.png
LOGO_CANAL       = resolver_asset("logo_canal.png", "logo_canal")
TWITCH_LOGO      = resolver_asset("logo_twitch.png", "logo_twitch")
PLANTILLA        = resolver_asset("plantilla_intro.png", "plantilla_intro")
OUTRO_TWITCH_BG  = resolver_asset("plantilla_outro.png", "plantilla_outro")
INTRO_PRE_RENDER = str(Path.cwd() / "intro_prerenderizada.mp4")
CARPETA_SALIDA   = "reels_linea90"

# Texto por defecto de la carátula / intro.
# En cada ejecución solo se pregunta el título superior.
# La intro ahora es una imagen vertical completa: plantilla_intro.png.
# El outro ahora puede venir de una imagen vertical completa: plantilla_outro.png.
TITULO_CARATULA_DEFECTO = "RADIOTRANSMISIÓN DEL MUNDIAL"
TWITCH_HANDLE_FIJO      = "@linea90min"
MENSAJE_TWITCH_FIJO     = "Todos los días radiotransmisión del mundial por @linea90min"
# Compatibilidad interna si alguna función antigua lo consulta.
TWITCH_HANDLE_DEFECTO   = TWITCH_HANDLE_FIJO
MENSAJE_TWITCH_DEFECTO  = MENSAJE_TWITCH_FIJO

# CTA compacto dentro del clip. No depende del cierre: aparece antes para empujar a Twitch.
TWITCH_CTA_LINEA_1 = "EN VIVO EN TWITCH"
TWITCH_CTA_LINEA_2 = "Reacciones completas + chat en directo"
TWITCH_CTA_HANDLE  = TWITCH_HANDLE_FIJO

# Calidad percibida para fuentes 720p: nitidez suave después del layout completo.
# No cambia recortes ni encuadres; solo mejora bordes después del escalado.
NITIDEZ_SUAVE_FILTRO = "unsharp=5:5:0.28:3:3:0.10"

# ================== MODO PROFESIONAL TIPO OPUSCLIP ==================

# Flujo nuevo por defecto:
# 1) El video real arranca de una vez con hook grande.
# 2) Entra una carátula/branding muy corta.
# 3) Continúa el clip con subtítulo visual opcional y barra de progreso.
# 4) Cierra con una pantalla breve enfocada solo en Twitch.
HOOK_DURACION_DEFECTO   = 1.15
OUTRO_TWITCH_DURACION   = 1.50
MOSTRAR_BARRA_PROGRESO  = True
NORMALIZAR_AUDIO        = True
USAR_CIERRE_TWITCH      = True
ALERTA_DURACION_REDES   = 50.0

DURACION_INTRO   = 0.9   # Branding corto tipo redes
TRANSICION       = 0.0   # En modo profesional se inserta branding, no se hace xfade

# Si quieres forzar FPS fijo, pon 30.
# Si quieres usar el FPS detectado del video, deja None.
FPS_FORZADO = None

# Calidad por defecto: fast / balanced / high
CALIDAD_DEFECTO = "balanced"

# Encoder:
# smart = recomendado: prueba encoders reales disponibles y evita GPU falsa
# auto/hw = intenta hardware usable y cae a libx264
# también puedes poner explícitamente: libx264, h264_amf, h264_nvenc, h264_qsv, h264_videotoolbox
ENCODER_DEFECTO = "smart"

AUDIO_BITRATE = "192k"
SCALING_ALGO  = "lanczos"  # v4.1: más nitidez tras el escalado

# Nombre único por ejecución
NOMBRE_SESION = datetime.now().strftime("%Y%m%d_%H%M%S")


# ================== CALIBRACIÓN MARCADOR ==================

RECORTES_JSON       = "recortes_marcador_linea90.json"
FRAME_REFERENCIA    = "frame_referencia_marcador.jpg"
PREVIEW_CALIBRACION = "preview_marcador_linea90.jpg"
RECORTES_ACTIVOS    = None


# ================== LAYOUT VERTICAL 1080x1920 ==================

ANCHO_OUT = 1080
ALTO_OUT  = 1920

# v5.1: layout más lleno y menos franja negra inferior.
# H_MARCADOR_SRC protege el recorte original del stream; no lo cambies si tu encuadre fuente ya está calibrado.
H_TOP_PAD       = 28
H_MARCADOR_SRC  = 180
H_MARCADOR      = 190
H_GAP_MARCADOR  = 22
H_CAMARA        = 604  # Debe ser par para yuv420p/FFmpeg
H_TEXTO_INF     = ALTO_OUT - H_TOP_PAD - H_MARCADOR - H_GAP_MARCADOR - (H_CAMARA * 2)
H_INFO_SUP      = 216  # Debe ser par
H_INFO_INF      = H_TEXTO_INF - H_INFO_SUP

# FFmpeg con yuv420p exige dimensiones pares. Si alguna altura es impar,
# scale/crop la redondea y el layout termina en 1916/1918 px, haciendo fallar concat.
for _nombre, _valor in {
    "H_TOP_PAD": H_TOP_PAD,
    "H_MARCADOR": H_MARCADOR,
    "H_GAP_MARCADOR": H_GAP_MARCADOR,
    "H_CAMARA": H_CAMARA,
    "H_TEXTO_INF": H_TEXTO_INF,
    "H_INFO_SUP": H_INFO_SUP,
    "H_INFO_INF": H_INFO_INF,
}.items():
    if _valor <= 0 or _valor % 2 != 0:
        raise ValueError(f"{_nombre} debe ser positivo y par; valor actual: {_valor}")

if H_TOP_PAD + H_MARCADOR + H_GAP_MARCADOR + (H_CAMARA * 2) + H_TEXTO_INF != ALTO_OUT:
    raise ValueError("El layout vertical no suma exactamente 1920 px")

# Croquis de la carátula / intro:
# 1) Título superior cambiante
# 2) Carátula del canal con los representantes
# 3) Logo Twitch + mensaje fijo
H_INTRO_TITULO   = 360
H_INTRO_CARATULA = 1020
H_INTRO_TWITCH   = ALTO_OUT - H_INTRO_TITULO - H_INTRO_CARATULA

FUENTE_TEXTO = "Arial"

# En Windows, FFmpeg/drawtext puede fallar si Fontconfig no tiene configuración.
# Usar fontfile evita el error: "Fontconfig error: Cannot load default config file".
FONTFILE_WINDOWS = r"C\:/Windows/Fonts/arial.ttf"
FONT_DRAW = f"fontfile='{FONTFILE_WINDOWS}'"

# Fallback marcador antiguo:
# Recorta desde arriba: crop=iw:180:0:0
CROP_MARCADOR_FALLBACK = (
    "iw:"
    f"{H_MARCADOR_SRC}:"
    "0:"
    "0"
)

XFADE_OFFSET = 0.0  # Compatibilidad; el modo profesional usa concat, no offset de xfade

# Nota técnica v19.1:
# Se fuerza setsar=1 en intro, clip y cierre para evitar errores de FFmpeg tipo:
# "Input link ... SAR ... do not match" al concatenar segmentos con diferentes aspect ratios.


# ================== PRESETS DE CALIDAD ==================

PRESETS_CALIDAD = {
    "fast": {
        "crf": 28,
        "preset": "veryfast",
        "maxrate": "3500k",
        "bufsize": "7000k",
        "uso": "Pruebas rápidas / previews"
    },
    "balanced": {
        "crf": 23,
        "preset": "fast",
        "maxrate": "5000k",
        "bufsize": "10000k",
        "uso": "Buena calidad y velocidad"
    },
    "high": {
        "crf": 18,
        "preset": "slow",
        "maxrate": "8000k",
        "bufsize": "16000k",
        "uso": "Mejor calidad, archivo más pesado"
    },
    "ultra": {
        "crf": 16,
        "preset": "slow",
        "maxrate": "12000k",
        "bufsize": "24000k",
        "uso": "Máxima calidad local para fuentes 720p / archivo pesado"
    }
}


# ================== PRESETS POR PLATAFORMA / V4.1 ==================

# No cambian el encuadre de cámaras. Solo ajustan bitrate/audio/duración sugerida.
PLATFORM_PRESETS = {
    "generic": {
        "descripcion": "Exportación general compatible",
        "maxrate": None,
        "bufsize": None,
        "audio_target": -14.0,
        "max_duration": 60.0,
    },
    "tiktok": {
        "descripcion": "TikTok / feed vertical",
        "maxrate": "4500k",
        "bufsize": "9000k",
        "audio_target": -14.0,
        "max_duration": 50.0,
    },
    "instagram": {
        "descripcion": "Instagram Reels",
        "maxrate": "5000k",
        "bufsize": "10000k",
        "audio_target": -14.0,
        "max_duration": 60.0,
    },
    "shorts": {
        "descripcion": "YouTube Shorts",
        "maxrate": "6000k",
        "bufsize": "12000k",
        "audio_target": -14.0,
        "max_duration": 60.0,
    },
    "master": {
        "descripcion": "Máster de archivo / mejor calidad",
        "maxrate": "12000k",
        "bufsize": "24000k",
        "audio_target": -14.0,
        "max_duration": 90.0,
    },
}

DEFAULT_KEYWORDS_DEPORTIVOS = (
    "gol,golazo,gooo,penal,penalty,roja,tarjeta roja,var,atajada,atajadon,atajadón,"
    "increible,increíble,impresionante,locura,final,ultimo minuto,último minuto,"
    "remate,poste,palo,mano,falta,campeon,campeón,clasifica,eliminado"
)


# ======================== MODELO MEDIAINFO ========================

@dataclass
class MediaInfo:
    ruta: str
    duracion: Optional[float]
    size_bytes: Optional[int]
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    r_frame_rate: Optional[str]
    avg_frame_rate: Optional[str]
    video_codec: Optional[str]
    audio_codec: Optional[str]
    has_audio: bool
    audio_channels: Optional[int]
    video_start_time: float
    audio_start_time: float
    likely_vfr: bool


# ======================== UTILIDADES GENERALES ========================

def segundos_a_tiempo(s):
    if s is None:
        return "desconocido"

    h = int(s // 3600)
    m = int((s % 3600) // 60)
    seg = s % 60
    return f"{h:02d}:{m:02d}:{seg:05.2f}"


def tiempo_a_segundos(t):
    t = t.strip()
    p = t.split(":")

    try:
        if len(p) == 3:
            return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
        if len(p) == 2:
            return int(p[0]) * 60 + float(p[1])
        return float(t)
    except ValueError:
        return None


def parse_fraction(frac):
    if not frac or frac == "0/0":
        return None

    try:
        if "/" in frac:
            num, den = frac.split("/")
            num = float(num)
            den = float(den)
            if den == 0:
                return None
            return num / den

        return float(frac)
    except Exception:
        return None


def escape_ffmpeg_text(text):
    if text is None:
        return ""

    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    text = text.replace(",", "\\,")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("\n", " ")
    text = text.replace("\r", " ")
    return text


def limpiar_nombre_archivo(nombre):
    nombre = nombre.strip().lower()

    reemplazos = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
        "ü": "u"
    }

    for original, nuevo in reemplazos.items():
        nombre = nombre.replace(original, nuevo)

    nombre = re.sub(r"[^\w\s-]", "", nombre)
    nombre = re.sub(r"\s+", "_", nombre)
    nombre = re.sub(r"_+", "_", nombre)
    nombre = nombre.strip("_-")

    return nombre




def envolver_texto(texto, max_chars=28, max_lineas=3):
    """Divide un texto corto en líneas centradas para drawtext."""
    texto = (texto or "").strip()

    if not texto:
        return []

    palabras = texto.split()
    lineas = []
    actual = ""

    for palabra in palabras:
        candidato = palabra if not actual else actual + " " + palabra

        if len(candidato) <= max_chars:
            actual = candidato
        else:
            if actual:
                lineas.append(actual)
            actual = palabra

            if len(lineas) >= max_lineas:
                break

    if actual and len(lineas) < max_lineas:
        lineas.append(actual)

    if len(lineas) > max_lineas:
        lineas = lineas[:max_lineas]

    # Si el título era demasiado largo, marca la última línea con puntos.
    texto_reconstruido = " ".join(lineas)
    if len(texto_reconstruido) < len(texto) and lineas:
        lineas[-1] = lineas[-1].rstrip(" .") + "..."

    return lineas


def filtros_drawtext_lineas(label_in, label_out, lineas, fontsize, color, y_inicial, line_height):
    """Construye una cadena de drawtext por cada línea y deja un label final."""
    if not lineas:
        lineas = [""]

    cadena = f"[{label_in}]"

    for i, linea in enumerate(lineas):
        etiqueta_intermedia = label_out if i == len(lineas) - 1 else f"{label_out}_{i}"
        texto = escape_ffmpeg_text(linea)
        y = y_inicial + i * line_height
        cadena += (
            f"drawtext={FONT_DRAW}:text='{texto}':fontsize={fontsize}:"
            f"fontcolor={color}:x=(w-text_w)/2:y={y}"
        )

        if i == len(lineas) - 1:
            cadena += f"[{label_out}];"
        else:
            cadena += f"[{etiqueta_intermedia}];[{etiqueta_intermedia}]"

    return cadena



def filtros_drawtext_lineas_estilo(
    label_in,
    label_out,
    lineas,
    fontsize,
    color,
    y_inicial,
    line_height,
    borderw=0,
    bordercolor="black",
    box=False,
    boxcolor="black@0.55",
    boxborderw=18,
    enable=None,
    x_expr="(w-text_w)/2"
):
    """Crea una cadena de drawtext con estilo para hooks/subtítulos."""
    if not lineas:
        return f"[{label_in}]copy[{label_out}];"

    cadena = f"[{label_in}]"

    for i, linea in enumerate(lineas):
        etiqueta_intermedia = f"{label_out}_tmp{i}"
        texto = escape_ffmpeg_text(linea)
        y = y_inicial + i * line_height

        cadena += (
            f"drawtext={FONT_DRAW}:text='{texto}':fontsize={fontsize}:"
            f"fontcolor={color}:x={x_expr}:y={y}:"
            f"borderw={borderw}:bordercolor={bordercolor}"
        )

        if box:
            cadena += f":box=1:boxcolor={boxcolor}:boxborderw={boxborderw}"

        if enable:
            cadena += f":enable='{enable}'"

        if i == len(lineas) - 1:
            cadena += f"[{label_out}];"
        else:
            cadena += f"[{etiqueta_intermedia}];[{etiqueta_intermedia}]"

    return cadena


def respuesta_si_no(prompt, default=True):
    sufijo = "[S/n]" if default else "[s/N]"
    r = input(f"  {prompt} {sufijo}: ").strip().lower()
    if not r:
        return default
    return r in ("s", "si", "sí", "y", "yes")


def hook_por_defecto(texto_gol):
    texto = (texto_gol or "").strip()
    if not texto:
        return "¡MOMENTO CLAVE EN VIVO!"
    texto = texto.upper()
    texto = texto.replace("GOLAZO", "¡GOLAZO")
    if not texto.startswith("¡"):
        texto = "¡" + texto
    if not texto.endswith("!"):
        texto = texto + "!"
    return texto


def normalizar_handle_twitch(handle):
    handle = (handle or "").strip()

    if not handle:
        handle = TWITCH_HANDLE_DEFECTO

    if not handle.startswith("@"):
        handle = "@" + handle

    return handle


def preparar_mensaje_twitch(mensaje, handle):
    mensaje = (mensaje or "").strip()

    if not mensaje:
        mensaje = MENSAJE_TWITCH_DEFECTO

    return mensaje.replace("{handle}", handle)


def pedir_datos_caratula(args):
    """Muestra los assets activos.
    En esta version la intro ya viene diseñada como imagen vertical completa.
    Por eso no se pregunta titulo ni se dibujan textos encima de la intro.
    """
    print("\n" + "─" * 70)
    print("  ASSETS DE BRANDING / TWITCH")
    print("  Estructura esperada en la carpeta:")
    print("   - logo_canal.png")
    print("   - logo_twitch.png")
    print("   - plantilla_intro.png")
    print("   - plantilla_outro.png")
    print("─" * 70)

    datos = {
        "titulo": TITULO_CARATULA_DEFECTO,
        "mensaje": MENSAJE_TWITCH_FIJO,
    }

    print("\n  ✅ Assets configurados:")
    print(f"     Intro vertical : {PLANTILLA}")
    print(f"     Outro Twitch   : {OUTRO_TWITCH_BG if os.path.exists(OUTRO_TWITCH_BG) else 'no encontrado; usaré fallback generado'}")
    print(f"     Logo canal     : {LOGO_CANAL}")
    print(f"     Logo Twitch    : {TWITCH_LOGO if os.path.exists(TWITCH_LOGO) else 'no encontrado; usaré texto TWITCH'}")

    return datos

def verificar_binarios():
    faltantes = []

    if not shutil.which("ffmpeg"):
        faltantes.append("ffmpeg")

    if not shutil.which("ffprobe"):
        faltantes.append("ffprobe")

    if faltantes:
        print("❌ Faltan programas necesarios en el PATH:")
        for f in faltantes:
            print(f"   - {f}")

        print("\nInstala FFmpeg y asegúrate de que ffmpeg y ffprobe funcionen desde la terminal.")
        sys.exit(1)


def detectar_cpu_logica():
    """Cantidad de hilos lógicos disponibles para FFmpeg/libx264."""
    try:
        return max(1, int(os.cpu_count() or 1))
    except Exception:
        return 1


def threads_ffmpeg_recomendados():
    """
    Evita saturar todo el CPU. En equipos Ryzen 5 5600G / 12 hilos,
    8-10 hilos suele ser más estable que usar el 100%.
    """
    hilos = detectar_cpu_logica()
    if hilos >= 12:
        return min(10, hilos - 2)
    if hilos >= 8:
        return min(6, hilos - 2)
    return max(1, hilos)


def preset_cpu_ajustado(preset_calidad):
    """
    Ajusta libx264 para estabilidad/calidad en PCs sin encoder GPU real.
    Mantiene CRF, pero evita presets muy lentos para renders masivos.
    """
    preset = dict(preset_calidad)
    if preset.get("preset") == "slow":
        preset["preset"] = "medium"
    return preset


def verificar_opencv():
    try:
        import cv2  # noqa
        import numpy  # noqa
        return True
    except ImportError:
        print("\n⚠️ Falta OpenCV o NumPy para calibrar con mouse.")
        print("Instala con:")
        print("   pip install opencv-python numpy")
        return False


def abrir_archivo(ruta):
    if not os.path.exists(ruta):
        return

    try:
        os.startfile(ruta)
    except AttributeError:
        print(f"  ℹ️ Abre manualmente: {ruta}")
    except OSError:
        print(f"  ℹ️ Abre manualmente: {ruta}")


def formatear_size(size_bytes):
    if not size_bytes:
        return "desconocido"

    mb = size_bytes / 1_048_576

    if mb < 1024:
        return f"{mb:.1f} MB"

    gb = mb / 1024
    return f"{gb:.2f} GB"


def ruta_absoluta_segura(ruta):
    return str(Path(ruta).expanduser().resolve())


def escape_ffmpeg_filter_path(path):
    """Escapa rutas para usarlas dentro de filtros FFmpeg como subtitles/fontsdir."""
    if not path:
        return ""
    s = ruta_absoluta_segura(path).replace("\\", "/")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    return s


def buscar_fuente_sistema():
    candidatos = [
        os.environ.get("LINEA90_FONT"),
        r"C:/Windows/Fonts/arialbd.ttf",
        r"C:/Windows/Fonts/arial.ttf",
        r"C:/Windows/Fonts/impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]

    for c in candidatos:
        if c and os.path.exists(c):
            return c

    return None


def configurar_fuente(font_file=None):
    """Configura FONT_DRAW sin tocar layout. Permite usar Anton/Montserrat/etc."""
    global FONT_DRAW, FONTFILE_WINDOWS

    elegido = font_file or buscar_fuente_sistema() or FONTFILE_WINDOWS

    if elegido and os.path.exists(elegido):
        FONTFILE_WINDOWS = elegido.replace("\\", "/")
        FONT_DRAW = f"fontfile='{escape_ffmpeg_filter_path(FONTFILE_WINDOWS)}'"
        print(f"🔤 Fuente activa: {FONTFILE_WINDOWS}")
    else:
        # Fallback por nombre si no hay ruta válida.
        FONT_DRAW = f"font='{FUENTE_TEXTO}'"
        print(f"⚠️ No encontré fontfile válido. Usaré fuente por nombre: {FUENTE_TEXTO}")


def aplicar_preset_plataforma(args, preset_calidad):
    """Aplica ajustes de plataforma sin cambiar ANCHO_OUT/ALTO_OUT ni crops de cámaras."""
    preset = dict(preset_calidad)
    platform = getattr(args, "platform", "generic") or "generic"
    datos = PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["generic"])

    if datos.get("maxrate"):
        preset["maxrate"] = datos["maxrate"]
    if datos.get("bufsize"):
        preset["bufsize"] = datos["bufsize"]

    return preset, datos


def opciones_keyframes(fps_salida, desactivar=False, encoder="libx264"):
    if desactivar:
        return []

    try:
        gop = max(1, int(round(float(fps_salida) * 2)))
    except Exception:
        gop = 60

    # v4.1: keyframe cada 2 segundos para redes y reproducción más amigable.
    # En hardware dejamos solo -g para evitar flags no soportados por algunos builds.
    encoder = (encoder or "").lower()
    if encoder == "libx264":
        return ["-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0"]

    return ["-g", str(gop)]


def ejecutar_cmd(cmd, error_msg="Comando falló", capture=True, timeout=None):
    """Ejecuta un comando externo con timeout opcional y error legible."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{error_msg}: timeout después de {float(timeout or 0):.0f}s") from e

    if r.returncode != 0:
        stderr = r.stderr if capture else ""
        raise RuntimeError(f"{error_msg}:\n{stderr}")
    return r


# ======================== INFO MEDIA / FFPROBE ========================

def ejecutar_json_ffprobe(ruta):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,start_time,channels,channel_layout",
        "-of", "json",
        ruta
    ]

    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    if r.returncode != 0:
        raise RuntimeError(f"ffprobe falló:\n{r.stderr}")

    return json.loads(r.stdout or "{}")


def obtener_info(ruta):
    try:
        data = ejecutar_json_ffprobe(ruta)
    except Exception as e:
        print(f"⚠️ No se pudo leer metadata con ffprobe: {e}")
        return MediaInfo(
            ruta=ruta,
            duracion=None,
            size_bytes=None,
            width=None,
            height=None,
            fps=None,
            r_frame_rate=None,
            avg_frame_rate=None,
            video_codec=None,
            audio_codec=None,
            has_audio=False,
            audio_channels=None,
            video_start_time=0.0,
            audio_start_time=0.0,
            likely_vfr=False
        )

    formato = data.get("format", {}) or {}
    streams = data.get("streams", []) or []

    video = None
    audio = None

    for st in streams:
        if st.get("codec_type") == "video" and video is None:
            video = st
        elif st.get("codec_type") == "audio" and audio is None:
            audio = st

    duracion = None
    size_bytes = None

    try:
        duracion = float(formato.get("duration")) if formato.get("duration") else None
    except Exception:
        duracion = None

    try:
        size_bytes = int(formato.get("size")) if formato.get("size") else None
    except Exception:
        size_bytes = None

    width = None
    height = None
    fps = None
    r_frame_rate = None
    avg_frame_rate = None
    video_codec = None
    video_start_time = 0.0

    if video:
        width = int(video.get("width", 0)) or None
        height = int(video.get("height", 0)) or None
        video_codec = video.get("codec_name")
        r_frame_rate = video.get("r_frame_rate")
        avg_frame_rate = video.get("avg_frame_rate")

        fps = parse_fraction(avg_frame_rate) or parse_fraction(r_frame_rate)

        try:
            video_start_time = float(video.get("start_time") or 0.0)
        except Exception:
            video_start_time = 0.0

    audio_codec = None
    audio_channels = None
    audio_start_time = 0.0

    if audio:
        audio_codec = audio.get("codec_name")

        try:
            audio_channels = int(audio.get("channels")) if audio.get("channels") else None
        except Exception:
            audio_channels = None

        try:
            audio_start_time = float(audio.get("start_time") or 0.0)
        except Exception:
            audio_start_time = 0.0

    has_audio = audio is not None

    fps_r = parse_fraction(r_frame_rate)
    fps_avg = parse_fraction(avg_frame_rate)

    likely_vfr = False

    if fps_r and fps_avg:
        likely_vfr = abs(fps_r - fps_avg) > 0.01

    return MediaInfo(
        ruta=ruta,
        duracion=duracion,
        size_bytes=size_bytes,
        width=width,
        height=height,
        fps=fps,
        r_frame_rate=r_frame_rate,
        avg_frame_rate=avg_frame_rate,
        video_codec=video_codec,
        audio_codec=audio_codec,
        has_audio=has_audio,
        audio_channels=audio_channels,
        video_start_time=video_start_time,
        audio_start_time=audio_start_time,
        likely_vfr=likely_vfr
    )


def elegir_fps(media_info):
    if FPS_FORZADO:
        return float(FPS_FORZADO)

    if media_info.fps and 1 <= media_info.fps <= 120:
        return round(media_info.fps, 3)

    return 30.0


def imprimir_info_media(info, fps_salida):
    print("✅ Stream cargado:")
    print(f"   Archivo       : {os.path.basename(info.ruta)}")
    print(f"   Duración      : {segundos_a_tiempo(info.duracion)}")
    print(f"   Tamaño        : {formatear_size(info.size_bytes)}")
    print(f"   Resolución    : {info.width or '?'}x{info.height or '?'}")
    print(f"   Video codec   : {info.video_codec or 'desconocido'}")
    print(f"   Audio codec   : {info.audio_codec or 'sin audio'}")
    print(f"   Audio canales : {info.audio_channels if info.audio_channels else 'sin audio'}")
    print(f"   FPS entrada   : {info.fps or 'desconocido'}")
    print(f"   FPS salida    : {fps_salida}")

    if info.likely_vfr:
        print("   ⚠️ Posible VFR : sí. Se forzará salida CFR con filtro fps.")

    if not info.has_audio:
        print("   ⚠️ Audio       : no detectado. Se generará silencio.")

    if abs(info.video_start_time - info.audio_start_time) > 0.05:
        print(
            "   ⚠️ Start time  : video/audio tienen start_time diferente "
            f"({info.video_start_time:.3f}s vs {info.audio_start_time:.3f}s)."
        )

    print()


# ======================== ENCODERS ========================

def obtener_encoders_ffmpeg():
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True
        )
        return r.stdout + r.stderr
    except Exception:
        return ""


def encoder_disponible_en_ffmpeg(encoder, encoders_text=None):
    encoders_text = encoders_text if encoders_text is not None else obtener_encoders_ffmpeg()
    return bool(encoder and encoder in encoders_text)


def probar_encoder_real(encoder):
    """
    Prueba real: no basta con que FFmpeg liste un encoder.
    En algunos equipos aparece h264_nvenc aunque no haya NVIDIA funcional.
    Esta prueba evita que el script falle clip por clip.
    """
    if not encoder:
        return False

    try:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-v", "error",
            "-f", "lavfi",
            "-i", "color=c=black:s=128x128:r=30:d=1",
            "-an",
            "-c:v", encoder,
            "-pix_fmt", "yuv420p",
            "-t", "1",
            "-f", "null",
            "-"
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def detectar_gpu_probable_windows():
    """
    Detección ligera para orientar el orden de pruebas.
    La decisión final siempre la toma probar_encoder_real().
    """
    if os.name != "nt":
        return "desconocida"

    try:
        r = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "name"],
            capture_output=True,
            text=True,
            timeout=8
        )
        salida = (r.stdout or "").lower()
        if "nvidia" in salida:
            return "nvidia"
        if "amd" in salida or "radeon" in salida:
            return "amd"
        if "intel" in salida:
            return "intel"
    except Exception:
        pass

    return "desconocida"


def elegir_encoder(modo):
    modo = (modo or "smart").strip().lower()
    if modo == "auto":
        modo = "smart"

    encoders = obtener_encoders_ffmpeg()
    gpu_probable = detectar_gpu_probable_windows()

    if gpu_probable == "amd":
        candidatos_hw = ["h264_amf", "h264_qsv", "h264_nvenc", "h264_videotoolbox"]
    elif gpu_probable == "nvidia":
        candidatos_hw = ["h264_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox"]
    elif gpu_probable == "intel":
        candidatos_hw = ["h264_qsv", "h264_amf", "h264_nvenc", "h264_videotoolbox"]
    else:
        candidatos_hw = ["h264_amf", "h264_qsv", "h264_nvenc", "h264_videotoolbox"]

    if modo in ("smart", "hw"):
        print(f"🧠 Modo encoder inteligente activo. GPU probable: {gpu_probable}")
        for enc in candidatos_hw:
            if encoder_disponible_en_ffmpeg(enc, encoders):
                print(f"🔎 Probando encoder hardware real: {enc}")
                if probar_encoder_real(enc):
                    print(f"✅ Encoder hardware usable: {enc}")
                    return enc
                print(f"⚠️ {enc} aparece en FFmpeg, pero no funciona en este equipo.")

        print("✅ Usaré libx264 estable por CPU.")
        return "libx264"

    if modo == "libx264":
        return "libx264"

    if encoder_disponible_en_ffmpeg(modo, encoders):
        if modo.startswith("h264_") and modo != "libx264":
            print(f"🔎 Probando encoder solicitado: {modo}")
            if probar_encoder_real(modo):
                print(f"✅ Encoder solicitado usable: {modo}")
                return modo
            print(f"⚠️ Encoder '{modo}' aparece, pero no funciona. Usaré libx264.")
            return "libx264"
        return modo

    print(f"⚠️ Encoder '{modo}' no encontrado en FFmpeg. Usaré libx264.")
    return "libx264"


def opciones_video_encoder(encoder, preset_calidad):
    preset_calidad = preset_cpu_ajustado(preset_calidad) if encoder == "libx264" else dict(preset_calidad)

    crf = str(preset_calidad["crf"])
    preset = preset_calidad["preset"]
    maxrate = preset_calidad["maxrate"]
    bufsize = preset_calidad["bufsize"]

    if encoder == "libx264":
        return [
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            "-crf", crf,
            "-preset", preset,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-threads", str(threads_ffmpeg_recomendados())
        ]

    if encoder == "h264_amf":
        return [
            "-c:v", "h264_amf",
            "-pix_fmt", "yuv420p",
            "-quality", "balanced",
            "-usage", "transcoding",
            "-b:v", maxrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize
        ]

    if encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-pix_fmt", "yuv420p",
            "-preset", "p4",
            "-tune", "hq",
            "-b:v", maxrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize
        ]

    if encoder == "h264_qsv":
        return [
            "-c:v", "h264_qsv",
            "-pix_fmt", "nv12",
            "-preset", "medium",
            "-global_quality", "24",
            "-b:v", maxrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize
        ]

    if encoder == "h264_videotoolbox":
        return [
            "-c:v", "h264_videotoolbox",
            "-pix_fmt", "yuv420p",
            "-b:v", maxrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize
        ]

    return [
        "-c:v", encoder,
        "-pix_fmt", "yuv420p",
        "-b:v", maxrate,
        "-maxrate", maxrate,
        "-bufsize", bufsize
    ]


# ======================== CALIBRACIÓN MARCADOR ========================

def hacer_par(v):
    v = int(round(v))

    if v < 0:
        v = 0

    if v % 2 != 0:
        v -= 1

    return max(v, 0)


def clamp_crop(crop, frame_w, frame_h):
    x = hacer_par(crop["x"])
    y = hacer_par(crop["y"])
    w = hacer_par(crop["w"])
    h = hacer_par(crop["h"])

    w = max(2, min(w, frame_w))
    h = max(2, min(h, frame_h))

    x = max(0, min(x, frame_w - w))
    y = max(0, min(y, frame_h - h))

    return {
        "x": hacer_par(x),
        "y": hacer_par(y),
        "w": hacer_par(w),
        "h": hacer_par(h)
    }


def crop_a_expr(crop):
    return f"{crop['w']}:{crop['h']}:{crop['x']}:{crop['y']}"


def crop_expr(nombre):
    global RECORTES_ACTIVOS

    if RECORTES_ACTIVOS and nombre in RECORTES_ACTIVOS:
        return crop_a_expr(RECORTES_ACTIVOS[nombre])

    if nombre == "marcador":
        return CROP_MARCADOR_FALLBACK

    raise ValueError(f"Recorte desconocido: {nombre}")


def extraer_frame_cv2(stream, tiempo):
    import cv2

    cap = cv2.VideoCapture(stream)

    if not cap.isOpened():
        raise RuntimeError("No se pudo abrir el video con OpenCV.")

    cap.set(cv2.CAP_PROP_POS_MSEC, tiempo * 1000)

    ok, frame = cap.read()
    cap.release()

    if not ok or frame is None:
        raise RuntimeError("No se pudo extraer frame para calibración.")

    return frame


def guardar_frame_referencia(frame):
    import cv2

    cv2.imwrite(FRAME_REFERENCIA, frame)
    print(f"🖼️ Frame de referencia creado: {FRAME_REFERENCIA}")
    abrir_archivo(FRAME_REFERENCIA)


def guardar_recortes(recortes, frame_w, frame_h):
    data = {
        "version": 1,
        "tipo": "marcador_linea90",
        "frame_w": frame_w,
        "frame_h": frame_h,
        "creado": datetime.now().isoformat(timespec="seconds"),
        "recortes": recortes,
    }

    with open(RECORTES_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"✅ Recorte del marcador guardado en: {RECORTES_JSON}")


def cargar_recortes_guardados(frame_w, frame_h):
    if not os.path.exists(RECORTES_JSON):
        return None

    try:
        with open(RECORTES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        recortes = data.get("recortes")

        if not recortes or "marcador" not in recortes:
            return None

        json_w = int(data.get("frame_w", frame_w))
        json_h = int(data.get("frame_h", frame_h))

        if json_w != frame_w or json_h != frame_h:
            print("⚠️ El recorte guardado es de otra resolución.")
            print(f"   Guardado: {json_w}x{json_h}")
            print(f"   Actual  : {frame_w}x{frame_h}")
            return None

        recortes["marcador"] = clamp_crop(recortes["marcador"], frame_w, frame_h)
        return recortes

    except Exception as e:
        print(f"⚠️ No se pudo cargar el recorte guardado: {e}")
        return None


def selector_manual_marcador(frame):
    import cv2

    alto, ancho = frame.shape[:2]

    print("\n🖱️ Calibración manual del marcador.")
    print("   Selecciona SOLO el marcador o la franja superior que quieres usar.")
    print("   ENTER o ESPACIO confirma.")
    print("   C cancela.")

    ventana = "Selecciona MARCADOR - ENTER para confirmar"
    cv2.namedWindow(ventana, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(ventana, 1280, 720)

    roi = cv2.selectROI(
        ventana,
        frame,
        showCrosshair=True,
        fromCenter=False
    )

    cv2.destroyWindow(ventana)

    x, y, w, h = roi

    if w <= 0 or h <= 0:
        return None

    return clamp_crop(
        {"x": x, "y": y, "w": w, "h": h},
        ancho,
        alto
    )


def generar_preview_marcador(frame, recorte, ruta_salida):
    import cv2
    import numpy as np

    marcador = frame[
        recorte["y"]:recorte["y"] + recorte["h"],
        recorte["x"]:recorte["x"] + recorte["w"]
    ]

    if marcador.size <= 0:
        print("⚠️ El recorte del marcador está vacío.")
        return

    preview = np.zeros((H_MARCADOR, ANCHO_OUT, 3), dtype=np.uint8)

    marcador_resized = cv2.resize(
        marcador,
        (ANCHO_OUT, H_MARCADOR),
        interpolation=cv2.INTER_LANCZOS4
    )

    preview[:, :] = marcador_resized

    cv2.imwrite(ruta_salida, preview)
    print(f"🖼️ Preview del marcador creado: {ruta_salida}")
    abrir_archivo(ruta_salida)


def calibrar_recorte_marcador(stream, dur_stream=None, force_recalibrate=False):
    global RECORTES_ACTIVOS

    print("\n" + "─" * 70)
    print("  CALIBRACIÓN DEL MARCADOR")
    print("  Puedes usar un recorte guardado o seleccionar el marcador con mouse.")
    print("─" * 70)

    if not verificar_opencv():
        print("⚠️ Se usará el recorte fijo antiguo del marcador.")
        return None

    try:
        frame_tmp = extraer_frame_cv2(stream, 0)
        frame_h, frame_w = frame_tmp.shape[:2]
    except Exception as e:
        print(f"⚠️ No se pudo extraer frame inicial: {e}")
        return None

    guardados = None

    if not force_recalibrate:
        guardados = cargar_recortes_guardados(frame_w, frame_h)

    if guardados:
        print("\n✅ Hay un recorte de marcador guardado.")
        usar = input("¿Usar recorte guardado? [S/n]: ").strip().lower()

        if usar in ("", "s", "si", "sí"):
            RECORTES_ACTIVOS = guardados
            return guardados

    while True:
        print("\nIndica un tiempo donde se vea bien el marcador.")
        print("Ejemplos: 01:25, 12:40, 00:15:30 o segundos.")

        t_str = input("Tiempo de referencia para calibrar marcador: ").strip()

        if not t_str:
            print("❌ Debes escribir un tiempo.")
            continue

        tiempo = tiempo_a_segundos(t_str)

        if tiempo is None:
            print("❌ Tiempo inválido.")
            continue

        if dur_stream and tiempo > dur_stream:
            print("❌ Ese tiempo supera la duración del video.")
            continue

        try:
            frame = extraer_frame_cv2(stream, tiempo)
        except Exception as e:
            print(f"❌ No se pudo extraer frame en ese tiempo: {e}")
            continue

        frame_h, frame_w = frame.shape[:2]
        guardar_frame_referencia(frame)

        recorte = selector_manual_marcador(frame)

        if recorte is None:
            print("⚠️ Selección cancelada.")
            usar_fallback = input("¿Usar marcador fijo antiguo? [s/N]: ").strip().lower()

            if usar_fallback in ("s", "si", "sí"):
                RECORTES_ACTIVOS = None
                return None

            continue

        try:
            generar_preview_marcador(frame, recorte, PREVIEW_CALIBRACION)
        except Exception as e:
            print(f"⚠️ No se pudo generar preview: {e}")

        print("\nRevisa el preview del marcador.")
        print("  S = está bien, guardar y continuar")
        print("  R = repetir selección")
        print("  N = usar este recorte sin guardarlo")
        print("  F = usar fallback fijo antiguo")

        resp = input("¿Qué quieres hacer? [S/R/N/F]: ").strip().lower()

        if resp in ("", "s", "si", "sí"):
            RECORTES_ACTIVOS = {"marcador": recorte}
            guardar_recortes(RECORTES_ACTIVOS, frame_w, frame_h)
            return RECORTES_ACTIVOS

        if resp == "n":
            RECORTES_ACTIVOS = {"marcador": recorte}
            return RECORTES_ACTIVOS

        if resp == "f":
            RECORTES_ACTIVOS = None
            return None

        if resp == "r":
            continue

        print("❌ Opción inválida.")



def matar_proceso_ffmpeg(proceso):
    """Mata FFmpeg y sus subprocesos si se queda colgado."""
    if proceso is None:
        return

    try:
        if proceso.poll() is not None:
            return
    except Exception:
        pass

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proceso.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True
            )
        else:
            proceso.kill()
    except Exception:
        try:
            proceso.kill()
        except Exception:
            pass


def timeout_clip_segundos(duracion_seg, args=None):
    """Tiempo máximo total permitido por clip."""
    try:
        mult = float(getattr(args, "clip_timeout_multiplier", 12.0))
    except Exception:
        mult = 12.0

    try:
        minimo_min = float(getattr(args, "clip_timeout_min", 8.0))
    except Exception:
        minimo_min = 8.0

    if duracion_seg is None:
        return minimo_min * 60

    return max(minimo_min * 60, float(duracion_seg) * mult)


def timeout_inactividad_segundos(args=None):
    """Tiempo máximo sin nuevas líneas de progreso de FFmpeg."""
    try:
        minutos = float(getattr(args, "clip_inactivity_timeout_min", 5.0))
    except Exception:
        minutos = 5.0
    return max(60.0, minutos * 60.0)



# ======================== PROGRESO ========================

def barra_progreso(cmd, descripcion="Procesando", duracion_seg=None, timeout_seg=None, inactivity_timeout_seg=None):
    """Ejecuta FFmpeg con progreso, timeout total y detección de avance real.

    La versión anterior reiniciaba el watchdog con cualquier línea de stderr.
    Ahora solo considera actividad cuando ``out_time`` aumenta; así un FFmpeg que
    sigue escribiendo mensajes pero dejó de codificar también se cancela.
    """
    import threading
    import queue

    ANCHO = 40
    re_t = re.compile(r"out_time_(?:ms|us)=(\d+)")
    spinner = itertools.cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])

    if timeout_seg is None:
        timeout_seg = max(8 * 60, float(duracion_seg or 0) * 12)
    if inactivity_timeout_seg is None:
        inactivity_timeout_seg = 5 * 60

    # FFmpeg informa avance con mayor regularidad y sin estadísticas duplicadas.
    cmd = list(cmd)
    if "-stats_period" not in cmd:
        try:
            idx = cmd.index("-progress")
            cmd[idx:idx] = ["-stats_period", "0.5"]
        except ValueError:
            pass

    proceso = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    q = queue.Queue()

    def lector_stderr():
        try:
            assert proceso.stderr is not None
            for linea in proceso.stderr:
                q.put(linea)
        except Exception as e:
            q.put(f"__LECTOR_STDERR_ERROR__ {e}")
        finally:
            q.put("__EOF_STDERR__")

    hilo = threading.Thread(target=lector_stderr, daemon=True)
    hilo.start()

    ha_empezado_render = False
    eof_stderr = False
    log_tail = []
    inicio = time.time()
    ultimo_avance = inicio
    ultimo_output = inicio
    ultimo_spinner = 0.0
    ultimo_seg = 0.0

    try:
        while True:
            ahora = time.time()

            if proceso.poll() is not None and (eof_stderr or q.empty()):
                break

            if ahora - inicio > timeout_seg:
                matar_proceso_ffmpeg(proceso)
                print()
                print(f"⏱️ Timeout total: {descripcion} superó {timeout_seg/60:.1f} min. Se canceló FFmpeg.")
                raise subprocess.TimeoutExpired(cmd, timeout_seg)

            # Antes de obtener el primer frame se usa el último output. Después,
            # el límite se mide contra el último avance real de out_time.
            referencia = ultimo_avance if ha_empezado_render else ultimo_output
            if ahora - referencia > inactivity_timeout_seg:
                matar_proceso_ffmpeg(proceso)
                print()
                print(
                    f"⏱️ Timeout por bloqueo: {descripcion} no avanzó durante "
                    f"{inactivity_timeout_seg/60:.1f} min."
                )
                raise subprocess.TimeoutExpired(cmd, inactivity_timeout_seg)

            try:
                linea = q.get(timeout=0.25)
            except queue.Empty:
                if ahora - ultimo_spinner > 0.5:
                    if ha_empezado_render:
                        estado = f"último avance {ultimo_seg:.1f}s"
                    else:
                        estado = "preparando filtros"
                    print(f"\r  {descripcion}: {estado}... {next(spinner)}", end="", flush=True)
                    ultimo_spinner = ahora
                continue

            if linea == "__EOF_STDERR__":
                eof_stderr = True
                continue

            linea = (linea or "").strip()
            ultimo_output = time.time()

            if linea:
                log_tail.append(linea)
                log_tail = log_tail[-40:]

            m = re_t.search(linea)
            if m:
                seg = int(m.group(1)) / 1_000_000
                if seg > ultimo_seg + 0.005:
                    ultimo_seg = seg
                    ultimo_avance = time.time()

                if seg > 0 and not ha_empezado_render:
                    ha_empezado_render = True
                    ultimo_avance = time.time()
                    print("\r" + " " * 100 + "\r", end="")

                if duracion_seg and seg > 0:
                    p = min(seg / duracion_seg, 1.0)
                    lleno = int(ANCHO * p)
                    barra = "█" * lleno + "░" * (ANCHO - lleno)
                    print(f"\r  {descripcion}: [{barra}] {p * 100:5.1f}%", end="", flush=True)
                elif seg > 0:
                    print(f"\r  {descripcion}: {seg:.1f}s...", end="", flush=True)

        try:
            proceso.wait(timeout=5)
        except subprocess.TimeoutExpired:
            matar_proceso_ffmpeg(proceso)
            raise

    except KeyboardInterrupt:
        matar_proceso_ffmpeg(proceso)
        print(f"\n🛑 {descripcion} cancelado por el usuario.")
        raise

    elapsed = time.time() - inicio
    if proceso.returncode == 0 and duracion_seg:
        barra = "█" * ANCHO
        print(f"\r  {descripcion}: [{barra}] 100.0%", end="", flush=True)
    print()

    if proceso.returncode != 0:
        print("\n❌ FFmpeg devolvió error. Últimas líneas:")
        lineas_error = [l for l in log_tail if not l.startswith("__")]
        for l in lineas_error:
            print(f"   {l}")

        try:
            ruta_log = os.path.abspath("ultimo_error_ffmpeg.txt")
            with open(ruta_log, "w", encoding="utf-8") as f:
                f.write("COMANDO FFmpeg:\n")
                f.write(subprocess.list2cmdline([str(x) for x in cmd]))
                f.write("\n\nÚLTIMAS LÍNEAS:\n")
                f.write("\n".join(lineas_error))
            print(f"   🧾 Diagnóstico guardado en: {ruta_log}")
        except Exception:
            pass

        raise subprocess.CalledProcessError(proceso.returncode, cmd)

    return elapsed


# ======================== INTRO ========================

def pre_renderizar_intro(fps_salida, preset_calidad, datos_caratula):
    """Genera la intro corta usando plantilla_intro.png como imagen completa 1080x1920.
    No dibuja textos adicionales: la plantilla ya trae logo, participantes y CTA Twitch.
    """
    if os.path.exists(INTRO_PRE_RENDER):
        try:
            os.remove(INTRO_PRE_RENDER)
        except OSError:
            pass

    print("🚀 Generando intro vertical desde plantilla_intro.png...")

    filtro_intro = (
        f"[0:v]fps={fps_salida},"
        f"scale={ANCHO_OUT}:{ALTO_OUT}:force_original_aspect_ratio=increase:flags={SCALING_ALGO},"
        f"crop={ANCHO_OUT}:{ALTO_OUT}:(iw-{ANCHO_OUT})/2:(ih-{ALTO_OUT})/2,"
        f"format=yuv420p,setsar=1,setpts=PTS-STARTPTS[intro_out]"
    )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v", "error",
        "-progress", "pipe:2",
        "-loop", "1",
        "-framerate", str(fps_salida),
        "-t", str(DURACION_INTRO),
        "-i", PLANTILLA,
        "-filter_complex", filtro_intro,
        "-map", "[intro_out]",
    ]

    cmd += opciones_video_encoder("libx264", preset_calidad)
    cmd += opciones_keyframes(fps_salida, False, "libx264")

    cmd += [
        "-t", str(DURACION_INTRO),
        "-movflags", "+faststart",
        "-y",
        INTRO_PRE_RENDER
    ]

    barra_progreso(cmd, descripcion="Intro vertical", duracion_seg=DURACION_INTRO, timeout_seg=120, inactivity_timeout_seg=45)
    print("✅ Intro vertical lista.\n")


# ======================== SUBTÍTULOS IA / V4.2 - V4.3 ========================

def tiempo_ass(seg):
    seg = max(0.0, float(seg or 0.0))
    h = int(seg // 3600)
    m = int((seg % 3600) // 60)
    s = seg % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def limpiar_texto_ass(texto):
    texto = str(texto or "").replace("\n", " ").replace("\r", " ").strip()
    texto = texto.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", texto)


def output_time_from_clip_time(t, hook_d):
    # El timeline final es: hook real -> intro silenciosa -> resto del clip.
    t = float(t or 0.0)
    if t <= hook_d:
        return t
    return t + DURACION_INTRO


def agrupar_palabras_subtitulos(words, max_words=4, max_chars=28, gap_reset=0.80):
    grupos = []
    actual = []
    last_end = None

    for w in words:
        palabra = limpiar_texto_ass(w.get("word") or w.get("text") or "")
        if not palabra:
            continue

        start = float(w.get("start", 0.0))
        end = float(w.get("end", start + 0.25))
        candidato_texto = " ".join([x["word"] for x in actual] + [palabra])

        cortar = False
        if actual and len(actual) >= max_words:
            cortar = True
        if actual and len(candidato_texto) > max_chars:
            cortar = True
        if last_end is not None and start - last_end > gap_reset:
            cortar = True

        if cortar and actual:
            grupos.append(actual)
            actual = []

        actual.append({"word": palabra, "start": start, "end": max(end, start + 0.18)})
        last_end = end

    if actual:
        grupos.append(actual)

    return grupos


def texto_grupo_ass(grupo, active_idx=None, highlight=True):
    partes = []
    for i, w in enumerate(grupo):
        palabra = limpiar_texto_ass(w["word"])
        if highlight and active_idx is not None and i == active_idx:
            partes.append(r"{\c&H0042C8F5&}" + palabra + r"{\c&H00FFFFFF&}")
        else:
            partes.append(palabra)
    return " ".join(partes)


def generar_ass_subtitulos(words, ruta_ass, duracion_clip, hook_d, font_name="Arial", word_highlight=True):
    """Genera ASS con resaltado palabra por palabra sin tocar el encuadre de cámaras."""
    grupos = agrupar_palabras_subtitulos(words)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ANCHO_OUT}
PlayResY: {ALTO_OUT}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Linea90Sub,{font_name},62,&H00FFFFFF,&H0042C8F5,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,1,5,1,2,70,70,560,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    eventos = []
    pos = r"{\an2\pos(540,1305)}"

    for grupo in grupos:
        if not grupo:
            continue

        if word_highlight:
            for idx, w in enumerate(grupo):
                start = output_time_from_clip_time(w["start"], hook_d)
                end = output_time_from_clip_time(w["end"], hook_d)
                if end <= start:
                    end = start + 0.20
                if start > duracion_clip + DURACION_INTRO + 1:
                    continue
                txt = pos + texto_grupo_ass(grupo, idx, highlight=True)
                eventos.append(f"Dialogue: 0,{tiempo_ass(start)},{tiempo_ass(end)},Linea90Sub,,0,0,0,,{txt}")
        else:
            start = output_time_from_clip_time(grupo[0]["start"], hook_d)
            end = output_time_from_clip_time(grupo[-1]["end"], hook_d)
            txt = pos + texto_grupo_ass(grupo, None, highlight=False)
            eventos.append(f"Dialogue: 0,{tiempo_ass(start)},{tiempo_ass(end)},Linea90Sub,,0,0,0,,{txt}")

    with open(ruta_ass, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(eventos) + "\n")

    return ruta_ass, len(eventos)


def importar_faster_whisper():
    try:
        from faster_whisper import WhisperModel
        return WhisperModel
    except Exception:
        return None


def duracion_wav_segundos(ruta_audio):
    try:
        with wave.open(ruta_audio, "rb") as wf:
            sr = wf.getframerate() or 1
            return wf.getnframes() / float(sr)
    except Exception:
        return None


def extraer_audio_segmento(stream, inicio, duracion, ruta_wav):
    duracion = max(0.1, float(duracion))
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
        "-progress", "pipe:2",
        "-ss", str(max(0.0, float(inicio))),
        "-t", str(duracion),
        "-i", stream,
        "-vn", "-ac", "1", "-ar", "16000",
        "-y", ruta_wav,
    ]
    barra_progreso(
        cmd,
        descripcion="Extrayendo audio para subtítulos",
        duracion_seg=duracion,
        timeout_seg=max(120.0, duracion * 4.0),
        inactivity_timeout_seg=60.0,
    )
    return ruta_wav


def _finalizar_proceso_multiprocessing(proceso):
    if proceso is None:
        return
    try:
        if proceso.is_alive():
            proceso.terminate()
            proceso.join(timeout=5)
        if proceso.is_alive() and hasattr(proceso, "kill"):
            proceso.kill()
            proceso.join(timeout=3)
    except Exception:
        pass


def _worker_whisper(ruta_audio, config, word_timestamps, cola):
    """Proceso aislado: permite cancelar Whisper si el modelo se bloquea."""
    try:
        from faster_whisper import WhisperModel

        cola.put({"tipo": "estado", "texto": f"cargando modelo {config['modelo']}"})
        model = WhisperModel(
            config["modelo"],
            device=config["device"],
            compute_type=config["compute_type"],
        )
        cola.put({"tipo": "estado", "texto": "modelo listo; transcribiendo"})

        segments, _info = model.transcribe(
            ruta_audio,
            language=config["language"],
            vad_filter=True,
            word_timestamps=word_timestamps,
            beam_size=config["beam_size"],
            condition_on_previous_text=False,
        )

        segmentos = []
        palabras = []
        ultimo_reporte = -1.0

        for seg in segments:
            texto = limpiar_texto_ass(getattr(seg, "text", ""))
            seg_end = float(getattr(seg, "end", 0.0))
            segmentos.append({
                "start": float(getattr(seg, "start", 0.0)),
                "end": seg_end,
                "text": texto,
            })
            if word_timestamps and getattr(seg, "words", None):
                for w in seg.words:
                    palabras.append({
                        "start": float(getattr(w, "start", 0.0)),
                        "end": float(getattr(w, "end", 0.0)),
                        "word": limpiar_texto_ass(getattr(w, "word", "")),
                    })

            if seg_end - ultimo_reporte >= 3.0:
                cola.put({"tipo": "progreso", "segundos": seg_end})
                ultimo_reporte = seg_end

        cola.put({"tipo": "resultado", "segmentos": segmentos, "palabras": palabras})
    except BaseException as e:
        import traceback
        cola.put({"tipo": "error", "texto": f"{e}\n{traceback.format_exc()}"})


def transcribir_audio_whisper(ruta_audio, args, word_timestamps=True):
    if importar_faster_whisper() is None:
        raise RuntimeError("Falta faster-whisper. Instala con: pip install faster-whisper")

    import multiprocessing as mp
    import queue as queue_std

    modelo = getattr(args, "whisper_model", "tiny")
    device = getattr(args, "whisper_device", "cpu")
    compute_type = getattr(args, "whisper_compute_type", "int8")
    language = getattr(args, "whisper_language", "es") or None
    beam_size = max(1, int(getattr(args, "whisper_beam_size", 1)))
    timeout_min = max(1.0, float(getattr(args, "whisper_timeout_min", 20.0)))
    total_audio = duracion_wav_segundos(ruta_audio)

    config = {
        "modelo": modelo,
        "device": device,
        "compute_type": compute_type,
        "language": language,
        "beam_size": beam_size,
    }

    # spawn también en Linux evita heredar estados de librerías GPU/BLAS.
    ctx = mp.get_context("spawn")
    cola = ctx.Queue()
    proceso = ctx.Process(
        target=_worker_whisper,
        args=(ruta_audio, config, word_timestamps, cola),
        daemon=True,
    )

    print(f"🧠 Whisper: modelo={modelo}, device={device}, compute={compute_type}, beam={beam_size}")
    proceso.start()

    inicio = time.time()
    ultimo_mensaje = 0.0
    spinner = itertools.cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
    resultado = None

    try:
        while True:
            transcurrido = time.time() - inicio
            if transcurrido > timeout_min * 60.0:
                _finalizar_proceso_multiprocessing(proceso)
                print()
                raise TimeoutError(f"Whisper superó el límite de {timeout_min:.1f} min y fue cancelado")

            try:
                mensaje = cola.get(timeout=0.5)
            except queue_std.Empty:
                if not proceso.is_alive():
                    # Da una oportunidad a que llegue el último mensaje de la cola.
                    try:
                        mensaje = cola.get(timeout=1.0)
                    except queue_std.Empty:
                        raise RuntimeError(f"Whisper terminó inesperadamente (código {proceso.exitcode})")
                else:
                    if time.time() - ultimo_mensaje >= 0.5:
                        print(
                            f"\r  Whisper trabajando... {transcurrido/60:.1f} min {next(spinner)}",
                            end="",
                            flush=True,
                        )
                        ultimo_mensaje = time.time()
                    continue

            tipo = mensaje.get("tipo")
            if tipo == "estado":
                print("\r" + " " * 100 + "\r", end="")
                print(f"  🧠 {mensaje.get('texto')}")
            elif tipo == "progreso":
                seg = float(mensaje.get("segundos", 0.0))
                if total_audio and total_audio > 0:
                    pct = min(100.0, seg / total_audio * 100.0)
                    print(f"\r  Whisper: {pct:5.1f}% ({seg:.0f}/{total_audio:.0f}s)", end="", flush=True)
                else:
                    print(f"\r  Whisper: {seg:.0f}s transcritos", end="", flush=True)
            elif tipo == "resultado":
                resultado = (mensaje.get("segmentos", []), mensaje.get("palabras", []))
                break
            elif tipo == "error":
                raise RuntimeError(mensaje.get("texto") or "Whisper falló")

        proceso.join(timeout=5)
        print("\r" + " " * 100 + "\r", end="")
        return resultado

    except KeyboardInterrupt:
        _finalizar_proceso_multiprocessing(proceso)
        print("\n🛑 Whisper cancelado por el usuario.")
        raise
    finally:
        _finalizar_proceso_multiprocessing(proceso)
        try:
            cola.close()
        except Exception:
            pass


def preparar_subtitulos_automaticos(stream, clip, indice, carpeta, args):
    if not getattr(args, "auto_subtitles", False):
        return None

    if clip.get("ass_subtitles"):
        return clip["ass_subtitles"]

    os.makedirs(carpeta, exist_ok=True)
    tmp_dir = os.path.join(carpeta, "_tmp_linea90")
    os.makedirs(tmp_dir, exist_ok=True)

    base = limpiar_nombre_archivo(clip.get("nombre_reel") or f"clip_{indice:02d}") or f"clip_{indice:02d}"
    wav_path = os.path.join(tmp_dir, f"{base}_audio.wav")
    ass_path = os.path.join(tmp_dir, f"{base}_subs.ass")

    print("📝 Generando subtítulos automáticos con faster-whisper...")

    try:
        extraer_audio_segmento(stream, clip["inicio"], clip["duracion"], wav_path)
        segmentos, palabras = transcribir_audio_whisper(wav_path, args, word_timestamps=True)

        if not palabras:
            print("⚠️ Whisper no devolvió palabras con timestamp. Se omiten subtítulos automáticos.")
            return None

        hook_d = min(float(HOOK_DURACION_DEFECTO), max(0.40, float(clip["duracion"]) - 0.20))
        font_name = getattr(args, "subtitle_font_name", None) or "Arial"
        _, total_events = generar_ass_subtitulos(
            palabras,
            ass_path,
            clip["duracion"],
            hook_d,
            font_name=font_name,
            word_highlight=not getattr(args, "no_word_highlight", False),
        )
        clip["ass_subtitles"] = ass_path
        clip["whisper_segments"] = segmentos
        print(f"✅ Subtítulos ASS listos: {os.path.basename(ass_path)} ({total_events} eventos)")
        return ass_path

    except Exception as e:
        print(f"⚠️ No se pudieron generar subtítulos automáticos: {e}")
        return None


# ======================== AUDIO PROFESIONAL / V4.4 ========================

def parse_loudnorm_json(stderr_text):
    txt = stderr_text or ""
    start = txt.rfind("{")
    end = txt.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(txt[start:end + 1])
    except Exception:
        return None


def medir_loudnorm_archivo(ruta_video, target_i=-14.0, target_tp=-1.5, target_lra=11.0):
    filtro = f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json"
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", ruta_video, "-af", filtro, "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    data = parse_loudnorm_json((r.stderr or "") + "\n" + (r.stdout or ""))
    if not data:
        raise RuntimeError("No se pudieron leer mediciones loudnorm")
    return data


def aplicar_loudnorm_two_pass(ruta_video, ruta_salida, medicion, target_i=-14.0, target_tp=-1.5, target_lra=11.0):
    filtro = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:"
        f"measured_I={medicion.get('input_i')}:"
        f"measured_TP={medicion.get('input_tp')}:"
        f"measured_LRA={medicion.get('input_lra')}:"
        f"measured_thresh={medicion.get('input_thresh')}:"
        f"offset={medicion.get('target_offset')}:"
        f"linear=true:print_format=summary,alimiter=limit=0.95"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-v", "error",
        "-i", ruta_video,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "copy",
        "-af", filtro,
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-y", ruta_salida
    ]
    ejecutar_cmd(cmd, "Falló loudnorm two-pass", timeout=600)


def normalizar_audio_two_pass_si_aplica(ruta_video, args):
    if getattr(args, "no_audio_normalize", False) or not getattr(args, "audio_two_pass", False):
        return ruta_video, None

    target_i = float(getattr(args, "audio_target", -14.0))
    target_tp = float(getattr(args, "audio_tp", -1.5))
    target_lra = float(getattr(args, "audio_lra", 11.0))

    print(f"🎚️ Loudnorm two-pass: target {target_i} LUFS...")
    medicion = medir_loudnorm_archivo(ruta_video, target_i, target_tp, target_lra)

    base, ext = os.path.splitext(ruta_video)
    tmp_out = base + "_loudnorm_tmp" + ext
    aplicar_loudnorm_two_pass(ruta_video, tmp_out, medicion, target_i, target_tp, target_lra)

    # Medición final para reporte, si es posible.
    final_medicion = None
    try:
        final_medicion = medir_loudnorm_archivo(tmp_out, target_i, target_tp, target_lra)
    except Exception:
        final_medicion = None

    os.replace(tmp_out, ruta_video)

    reporte = {
        "archivo": os.path.basename(ruta_video),
        "target_i": target_i,
        "target_tp": target_tp,
        "target_lra": target_lra,
        "medicion_inicial": medicion,
        "medicion_final": final_medicion,
        "generado": datetime.now().isoformat(timespec="seconds"),
    }

    ruta_reporte = base + "_audio_report.json"
    with open(ruta_reporte, "w", encoding="utf-8") as f:
        json.dump(reporte, f, indent=2, ensure_ascii=False)

    print(f"✅ Audio normalizado. Reporte: {os.path.basename(ruta_reporte)}")
    return ruta_video, ruta_reporte


# ======================== DETECCIÓN AUTOMÁTICA / V5.0 ========================

def extraer_audio_scan(stream, ruta_wav, duracion_stream=None):
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
        "-progress", "pipe:2",
        "-i", stream,
        "-vn", "-ac", "1", "-ar", "16000",
        "-y", ruta_wav,
    ]
    dur = float(duracion_stream or 0.0)
    barra_progreso(
        cmd,
        descripcion="Extrayendo audio para autodetección",
        duracion_seg=dur or None,
        timeout_seg=max(10 * 60.0, dur * 3.0) if dur else 60 * 60.0,
        inactivity_timeout_seg=120.0,
    )
    return ruta_wav


def leer_rms_por_ventana(ruta_wav, ventana_seg=1.0):
    """Calcula RMS de forma vectorizada y muestra avance.

    Evita el bucle muestra-a-muestra del código anterior, que podía ejecutar
    decenas de millones de operaciones Python en streams largos.
    """
    try:
        import numpy as np
    except ImportError as e:
        raise RuntimeError("La autodetección rápida requiere NumPy: pip install numpy") from e

    with wave.open(ruta_wav, "rb") as wf:
        sr = wf.getframerate()
        nframes = wf.getnframes()
        sampwidth = wf.getsampwidth()
        canales = wf.getnchannels()

        if sampwidth != 2:
            raise RuntimeError(f"WAV no compatible para RMS: {sampwidth * 8} bits; se esperaba PCM 16-bit")

        frames_por_ventana = max(1, int(sr * ventana_seg))
        total_ventanas = max(1, math.ceil(nframes / frames_por_ventana))
        rms = []
        ultimo_pct = -1
        inicio = time.time()

        for idx in range(total_ventanas):
            raw = wf.readframes(frames_por_ventana)
            if not raw:
                break

            data = np.frombuffer(raw, dtype="<i2")
            if canales > 1 and data.size >= canales:
                data = data[: (data.size // canales) * canales].reshape(-1, canales)[:, 0]

            if data.size:
                data_f = data.astype(np.float32) / 32768.0
                val = float(np.sqrt(np.mean(data_f * data_f, dtype=np.float64)))
            else:
                val = 0.0

            rms.append({"t": idx * ventana_seg, "rms": val})

            pct = int((idx + 1) * 100 / total_ventanas)
            if pct >= ultimo_pct + 2 or idx + 1 == total_ventanas:
                print(f"\r  Analizando intensidad del audio: {pct:3d}%", end="", flush=True)
                ultimo_pct = pct

        print(f"  ({time.time() - inicio:.1f}s)")
    return rms


def percentile(vals, pct):
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = (len(vals) - 1) * pct / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return vals[int(k)]
    return vals[f] * (c - k) + vals[c] * (k - f)


def eventos_por_picos_audio(rms, args):
    valores = [x["rms"] for x in rms]
    pct = float(getattr(args, "auto_threshold_percentile", 90.0))
    umbral = percentile(valores, pct)
    min_gap = float(getattr(args, "auto_min_gap", 20.0))

    eventos = []
    last_t = -9999.0
    for x in rms:
        t = x["t"]
        val = x["rms"]
        if val >= umbral and t - last_t >= min_gap:
            score = 40 + min(45, (val / (umbral + 1e-9)) * 12)
            eventos.append({
                "t": t,
                "score": score,
                "tipo": "audio_peak",
                "texto": "Pico de emoción / volumen",
                "rms": val,
            })
            last_t = t
    return eventos, umbral


def normalizar_keyword(s):
    s = (s or "").lower().strip()
    reemplazos = {"á":"a", "é":"e", "í":"i", "ó":"o", "ú":"u", "ñ":"n"}
    for a, b in reemplazos.items():
        s = s.replace(a, b)
    return s


def eventos_por_keywords_whisper(ruta_audio, args):
    keywords = [normalizar_keyword(k) for k in (getattr(args, "auto_keywords", "") or DEFAULT_KEYWORDS_DEPORTIVOS).split(",")]
    keywords = [k for k in keywords if k]

    if getattr(args, "no_auto_keywords", False):
        return []

    WhisperModel = importar_faster_whisper()
    if WhisperModel is None:
        print("⚠️ Para keywords automáticas instala faster-whisper. Continuaré solo con picos de audio.")
        return []

    print("🔎 Buscando keywords deportivas con Whisper...")
    segmentos, _ = transcribir_audio_whisper(ruta_audio, args, word_timestamps=False)
    eventos = []

    for seg in segmentos:
        txt_norm = normalizar_keyword(seg.get("text", ""))
        hits = [k for k in keywords if k in txt_norm]
        if hits:
            eventos.append({
                "t": float(seg.get("start", 0.0)),
                "score": 85 + min(10, len(hits) * 2),
                "tipo": "keyword",
                "texto": seg.get("text", "Momento clave"),
                "keywords": hits[:5],
            })

    return eventos


def fusionar_eventos(eventos, min_gap=18.0):
    eventos = sorted(eventos, key=lambda e: (e["t"], -e.get("score", 0)))
    fusionados = []
    for ev in eventos:
        if not fusionados or ev["t"] - fusionados[-1]["t"] > min_gap:
            fusionados.append(dict(ev))
        else:
            prev = fusionados[-1]
            if ev.get("score", 0) > prev.get("score", 0):
                # Conserva el mejor evento, pero suma señal del anterior.
                ev = dict(ev)
                ev["score"] = min(100, ev.get("score", 0) + 5)
                fusionados[-1] = ev
            else:
                prev["score"] = min(100, prev.get("score", 0) + 3)
    return fusionados


def clips_desde_eventos(eventos, dur_stream, args):
    pre = float(getattr(args, "auto_pre_roll", 8.0))
    post = float(getattr(args, "auto_post_roll", 32.0))
    max_clips = int(getattr(args, "auto_max_clips", 10))
    min_gap = float(getattr(args, "auto_min_gap", 20.0))

    candidatos = []
    for ev in eventos:
        inicio = max(0.0, ev["t"] - pre)
        fin = ev["t"] + post
        if dur_stream:
            fin = min(float(dur_stream), fin)
        if fin <= inicio + 5:
            continue
        texto_base = limpiar_texto_ass(ev.get("texto") or "Momento clave")
        if len(texto_base) > 55:
            texto_base = texto_base[:52].rstrip() + "..."
        clip = {
            "inicio": inicio,
            "fin": fin,
            "duracion": fin - inicio,
            "texto_gol": texto_base.upper() if ev.get("tipo") == "keyword" else "MOMENTO CLAVE",
            "minuto_gol": segundos_a_tiempo(ev["t"]),
            "mostrar_minuto": True,
            "hook_text": hook_por_defecto(texto_base if ev.get("tipo") == "keyword" else "Momento clave"),
            "subtitulo_visual": "",
            "nombre_reel": f"auto_{ev.get('tipo','momento')}_{int(ev['t']):06d}",
            "auto_score": round(float(ev.get("score", 0)), 1),
            "auto_tipo": ev.get("tipo"),
        }
        candidatos.append(clip)

    candidatos.sort(key=lambda c: c.get("auto_score", 0), reverse=True)
    seleccionados = []
    for c in candidatos:
        solapa = False
        for s in seleccionados:
            if abs(c["inicio"] - s["inicio"]) < min_gap or not (c["fin"] < s["inicio"] or c["inicio"] > s["fin"]):
                solapa = True
                break
        if not solapa:
            seleccionados.append(c)
        if len(seleccionados) >= max_clips:
            break

    seleccionados.sort(key=lambda c: c["inicio"])
    return seleccionados


def detectar_clips_automaticos(stream, info, args):
    tmp_dir = os.path.join(args.output_dir, "_tmp_linea90")
    os.makedirs(tmp_dir, exist_ok=True)
    wav_path = os.path.join(tmp_dir, "auto_scan_16000.wav")

    print("🤖 v5.0: detectando momentos con picos de audio + keywords...")
    extraer_audio_scan(stream, wav_path, info.duracion)

    rms = leer_rms_por_ventana(wav_path, ventana_seg=1.0)
    eventos_audio, umbral = eventos_por_picos_audio(rms, args)
    print(f"   Picos de audio detectados: {len(eventos_audio)} | umbral RMS p{getattr(args, 'auto_threshold_percentile', 90)} = {umbral:.5f}")

    eventos_kw = eventos_por_keywords_whisper(wav_path, args)
    if eventos_kw:
        print(f"   Eventos por keyword detectados: {len(eventos_kw)}")

    eventos = fusionar_eventos(eventos_audio + eventos_kw, min_gap=float(getattr(args, "auto_min_gap", 20.0)))
    clips = clips_desde_eventos(eventos, info.duracion, args)

    if clips:
        print(f"✅ v5.0 sugirió {len(clips)} clips automáticos.")
        for i, c in enumerate(clips, 1):
            print(
                f"   #{i:02d} score {c.get('auto_score', 0):>5} | "
                f"{segundos_a_tiempo(c['inicio'])} → {segundos_a_tiempo(c['fin'])} | "
                f"{c.get('auto_tipo')} | {c['texto_gol']}"
            )
    else:
        print("⚠️ No se detectaron clips automáticos suficientes.")

    return clips


# ======================== FILTRO VIDEO / AUDIO ========================

def filtergraph_unico(
    texto_gol,
    minuto_gol,
    fps_salida,
    has_audio,
    duracion_clip,
    hook_text="",
    subtitulo_visual="",
    usar_cierre_twitch=True,
    mostrar_barra=True,
    normalizar_audio=True,
    ass_subtitles=None,
    fonts_dir=None,
    audio_target_lufs=-16.0,
    usar_logo_twitch_clip=False,
    engagement_question="",
    comment_cta="",
    sharpen=True,
    twitch_logo_input_index=None,
    outro_bg_input_index=None,
    mostrar_minuto=True
):
    """Filtro profesional tipo OpusClip.

    Timeline de salida:
      1) Primeros segundos reales del clip con hook grande.
      2) Branding/caratula corta.
      3) Resto del clip con subtítulo visual opcional.
      4) Cierre Twitch corto opcional.
    """
    t1 = escape_ffmpeg_text(f"MIN {minuto_gol}")
    t2 = escape_ffmpeg_text(texto_gol)
    y_cam1_inicio = H_TOP_PAD + H_MARCADOR + H_GAP_MARCADOR
    y_cam2_inicio = y_cam1_inicio + H_CAMARA
    y_info_inicio = y_cam2_inicio + H_CAMARA

    hook_d = min(float(HOOK_DURACION_DEFECTO), max(0.40, float(duracion_clip) - 0.20))
    body_ini = hook_d
    dur_outro = float(OUTRO_TWITCH_DURACION) if usar_cierre_twitch else 0.0
    duracion_salida = float(duracion_clip) + float(DURACION_INTRO) + dur_outro

    hook_text = hook_text or hook_por_defecto(texto_gol)
    lineas_hook = envolver_texto(hook_text.upper(), max_chars=21, max_lineas=2)
    font_hook = 70 if len(lineas_hook) <= 1 else 62
    line_h_hook = int(font_hook * 1.18)
    y_hook = 255

    lineas_sub = envolver_texto(subtitulo_visual, max_chars=27, max_lineas=2) if subtitulo_visual else []
    font_sub = 56 if len(lineas_sub) <= 1 else 50
    line_h_sub = int(font_sub * 1.16)
    y_sub = y_cam2_inicio + H_CAMARA - 150

    # Cierre Twitch corto: máximo 2 segundos, una sola acción.
    lineas_outro_1 = ["TODOS LOS DÍAS POR TWITCH"]
    lineas_outro_2 = envolver_texto("Radiotransmisión del Mundial, reacción y minuto a minuto", max_chars=30, max_lineas=2)
    lineas_outro_3 = [TWITCH_HANDLE_FIJO]

    # Capa editorial de engagement. Se mantiene fuera del recorte de cámaras.
    q_eng = escape_ffmpeg_text((engagement_question or "").strip().upper())
    cta_comment = escape_ffmpeg_text((comment_cta or "").strip().upper())
    tw1 = escape_ffmpeg_text(TWITCH_CTA_LINEA_1)
    tw2 = escape_ffmpeg_text(TWITCH_CTA_LINEA_2)
    twh = escape_ffmpeg_text(TWITCH_CTA_HANDLE)
    tw_idx = twitch_logo_input_index if twitch_logo_input_index is not None else 3
    outro_idx = outro_bg_input_index

    titulo_superior = ""
    if mostrar_minuto:
        titulo_superior = (
            f"[fondo_info]"
            f"drawtext={FONT_DRAW}:text='{t1}':fontsize=28:fontcolor=white:"
            f"x=(w-text_w)/2:y=28:borderw=2:bordercolor=black,"
            f"drawtext={FONT_DRAW}:text='{t2}':fontsize=27:fontcolor=#F5C842:"
            f"x=(w-text_w)/2:y=72:borderw=2:bordercolor=black[info_top_texto];"
        )
        y_logo_canal = 118
    else:
        titulo_superior = (
            f"[fondo_info]"
            f"drawtext={FONT_DRAW}:text='{t2}':fontsize=31:fontcolor=#F5C842:"
            f"x=(w-text_w)/2:y=52:borderw=2:bordercolor=black[info_top_texto];"
        )
        y_logo_canal = 112

    video = (
        f"[0:v]fps={fps_salida},format=yuv420p,setsar=1,setpts=PTS-STARTPTS[intro];"
        f"[1:v]fps={fps_salida},format=yuv420p,setsar=1,split=3[stream_marc][left][right];"

        # 0. PAD SUPERIOR + MARCADOR + ESPACIO INFERIOR
        f"color=black:s={ANCHO_OUT}x{H_TOP_PAD}:r={fps_salida},format=yuv420p,setsar=1[top_pad];"
        f"color=black:s={ANCHO_OUT}x{H_GAP_MARCADOR}:r={fps_salida},format=yuv420p,setsar=1[gap_marcador];"

        # 1. MARCADOR ARRIBA / CALIBRADO
        f"[stream_marc]crop={crop_expr('marcador')},"
        f"scale={ANCHO_OUT}:{H_MARCADOR}:flags={SCALING_ALGO},"
        f"format=yuv420p,setsar=1[bloque_superior];"

        # 2. PARTICIPANTE IZQUIERDO
        f"[left]crop=iw/2:ih-{H_MARCADOR_SRC}:0:{H_MARCADOR_SRC},"
        f"scale={ANCHO_OUT}:{H_CAMARA}:force_original_aspect_ratio=increase:flags={SCALING_ALGO},"
        f"crop={ANCHO_OUT}:{H_CAMARA}:0:0,"
        f"format=yuv420p,setsar=1[cam1];"

        # 3. PARTICIPANTE DERECHO
        f"[right]crop=iw/2:ih-{H_MARCADOR_SRC}:iw/2:{H_MARCADOR_SRC},"
        f"scale={ANCHO_OUT}:{H_CAMARA}:force_original_aspect_ratio=increase:flags={SCALING_ALGO},"
        f"crop={ANCHO_OUT}:{H_CAMARA}:0:0,"
        f"format=yuv420p,setsar=1[cam2];"

        # 4. BLOQUE INFERIOR TOTAL
        f"color=black:s={ANCHO_OUT}x{H_TEXTO_INF}:r={fps_salida},"
        f"format=yuv420p,setsar=1,split=2[fondo_inf_info][fondo_inf_twitch];"

        # 5. SUB-BLOQUE SUPERIOR PARA INFO + LOGO CANAL
        f"[fondo_inf_info]crop={ANCHO_OUT}:{H_INFO_SUP}:0:0,format=yuv420p,setsar=1[fondo_info];"
        f"[fondo_inf_twitch]crop={ANCHO_OUT}:{H_INFO_INF}:0:{H_INFO_SUP},format=yuv420p,setsar=1[fondo_twitch];"

        # 6. LOGO DEL CANAL
        f"[2:v]format=rgba,scale=165:-1:flags={SCALING_ALGO}[logo_s];"
    )

    video += titulo_superior
    video += (
        f"[info_top_texto][logo_s]overlay=(W-w)/2:{y_logo_canal},"
        f"format=yuv420p,setsar=1[zona_info_top];"
    )

    video += (
        # 7. SUB-BLOQUE INFERIOR PARA CTA TWITCH
        f"[fondo_twitch]"
        f"drawtext={FONT_DRAW}:text='{tw1}':fontsize=32:fontcolor=white:"
        f"x=(w-text_w)/2:y=22:borderw=2:bordercolor=black,"
        f"drawtext={FONT_DRAW}:text='{tw2}':fontsize=24:fontcolor=white@0.92:"
        f"x=(w-text_w)/2:y=60:borderw=2:bordercolor=black[zona_twitch_base];"
    )

    if usar_logo_twitch_clip:
        video += (
            f"[{tw_idx}:v]format=rgba,scale=60:-1:flags={SCALING_ALGO}[tw_logo_small];"
            f"[zona_twitch_base][tw_logo_small]overlay=350:102[zona_twitch_0];"
            f"[zona_twitch_0]drawtext={FONT_DRAW}:text='{twh}':fontsize=34:fontcolor=#F5C842:"
            f"x=420:y=104:borderw=2:bordercolor=black,format=yuv420p,setsar=1[zona_twitch];"
        )
    else:
        video += (
            f"[zona_twitch_base]drawtext={FONT_DRAW}:text='TWITCH {twh}':fontsize=34:fontcolor=#F5C842:"
            f"x=(w-text_w)/2:y=104:borderw=2:bordercolor=black,format=yuv420p,setsar=1[zona_twitch];"
        )

    video += (
        # 8. BLOQUE INFERIOR UNIFICADO
        f"[zona_info_top][zona_twitch]vstack=inputs=2,format=yuv420p,setsar=1[zona_texto];"

        # 9. Layout final del clip real
        f"[top_pad][bloque_superior][gap_marcador][cam1][cam2][zona_texto]"
        f"vstack=inputs=6,"
        f"format=yuv420p,setsar=1[vid_base0];"
    )
    if lineas_sub:
        video += filtros_drawtext_lineas_estilo(
            label_in="vid_base0",
            label_out="vid_base",
            lineas=lineas_sub,
            fontsize=font_sub,
            color="white",
            y_inicial=y_sub,
            line_height=line_h_sub,
            borderw=5,
            bordercolor="black",
            box=True,
            boxcolor="black@0.40",
            boxborderw=16,
            enable="between(t,2.0,6.5)"
        )
    else:
        video += f"[vid_base0]copy[vid_base];"

    if q_eng:
        video += (
            f"[vid_base]drawtext={FONT_DRAW}:text='{q_eng}':fontsize=44:fontcolor=white:"
            f"x=(w-text_w)/2:y={y_cam1_inicio + 18}:borderw=5:bordercolor=black:"
            f"box=1:boxcolor=black@0.46:boxborderw=16:enable='between(t,3.0,8.0)'[vid_base_eng];"
        )
        base_label = "vid_base_eng"
    else:
        base_label = "vid_base"

    if cta_comment:
        video += (
            f"[{base_label}]drawtext={FONT_DRAW}:text='{cta_comment}':fontsize=40:fontcolor=#F5C842:"
            f"x=(w-text_w)/2:y={y_cam2_inicio + H_CAMARA - 82}:borderw=5:bordercolor=black:"
            f"box=1:boxcolor=black@0.42:boxborderw=14:enable='between(t,8.0,14.0)'[vid_base_cta];"
        )
        base_label = "vid_base_cta"

    video += (
        f"[{base_label}]split=2[vhooksrc][vbodysrc];"
        f"[vhooksrc]trim=0:{hook_d:.3f},setpts=PTS-STARTPTS,setsar=1[vhook0];"
    )

    video += filtros_drawtext_lineas_estilo(
        label_in="vhook0",
        label_out="v_hook",
        lineas=lineas_hook,
        fontsize=font_hook,
        color="#F5C842",
        y_inicial=y_hook,
        line_height=line_h_hook,
        borderw=6,
        bordercolor="black",
        box=True,
        boxcolor="black@0.52",
        boxborderw=20
    )

    video += (
        f"[vbodysrc]trim={body_ini:.3f}:{duracion_clip:.3f},setpts=PTS-STARTPTS,setsar=1[v_body];"
    )

    if usar_cierre_twitch:
        if outro_idx is not None:
            # v5.2: outro desde plantilla_outro.png como pantalla completa 1080x1920.
            video += (
                f"[{outro_idx}:v]fps={fps_salida},"
                f"scale={ANCHO_OUT}:{ALTO_OUT}:force_original_aspect_ratio=increase:flags={SCALING_ALGO},"
                f"crop={ANCHO_OUT}:{ALTO_OUT}:(iw-{ANCHO_OUT})/2:(ih-{ALTO_OUT})/2,"
                f"format=yuv420p,trim=0:{dur_outro:.3f},setpts=PTS-STARTPTS,setsar=1[v_outro];"
            )
        else:
            # Fallback si no existe plantilla_outro.png.
            video += (
                f"color=black:s={ANCHO_OUT}x{ALTO_OUT}:r={fps_salida}:d={dur_outro:.3f},setsar=1[outro_bg0];"
            )
            video += filtros_drawtext_lineas_estilo(
                label_in="outro_bg0",
                label_out="outro_bg1",
                lineas=lineas_outro_1,
                fontsize=58,
                color="white",
                y_inicial=620,
                line_height=68,
                borderw=4,
                bordercolor="black"
            )
            video += filtros_drawtext_lineas_estilo(
                label_in="outro_bg1",
                label_out="outro_bg2",
                lineas=lineas_outro_2,
                fontsize=44,
                color="white",
                y_inicial=760,
                line_height=58,
                borderw=3,
                bordercolor="black"
            )
            if usar_logo_twitch_clip:
                video += (
                    f"[{tw_idx}:v]format=rgba,scale=225:-1:flags={SCALING_ALGO}[tw_logo_outro];"
                    f"[outro_bg2][tw_logo_outro]overlay=(W-w)/2:930[outro_bg3];"
                )
            else:
                video += (
                    f"[outro_bg2]drawtext={FONT_DRAW}:text='TWITCH':fontsize=48:fontcolor=white:"
                    f"box=1:boxcolor=#9146FF@0.95:boxborderw=24:"
                    f"x=(w-text_w)/2:y=980:borderw=2:bordercolor=black[outro_bg3];"
                )
            video += filtros_drawtext_lineas_estilo(
                label_in="outro_bg3",
                label_out="v_outro",
                lineas=lineas_outro_3,
                fontsize=64,
                color="#F5C842",
                y_inicial=1115,
                line_height=70,
                borderw=5,
                bordercolor="black"
            )
        video += (
            f"[v_hook]setsar=1[v_hook_s];"
            f"[intro]setsar=1[intro_s];"
            f"[v_body]setsar=1[v_body_s];"
            f"[v_outro]setsar=1[v_outro_s];"
            f"[v_hook_s][intro_s][v_body_s][v_outro_s]concat=n=4:v=1:a=0[v_concat];"
        )
    else:
        video += (
            f"[v_hook]setsar=1[v_hook_s];"
            f"[intro]setsar=1[intro_s];"
            f"[v_body]setsar=1[v_body_s];"
            f"[v_hook_s][intro_s][v_body_s]concat=n=3:v=1:a=0[v_concat];"
        )

    # Fuerza CFR después de concat; evita que FFmpeg elija 25 fps por defecto
    # al mezclar video real, imágenes en loop, color sources e intro.
    video += f"[v_concat]fps={fps_salida},format=yuv420p,setsar=1[v_concat_cfr];"

    if mostrar_barra:
        video += (
            f"[v_concat_cfr]drawbox=x=0:y=0:w='min(iw,iw*t/{duracion_salida:.3f})':"
            f"h=10:color=#F5C842@0.95:t=fill,format=yuv420p,setsar=1[v_pre_quality];"
        )
    else:
        video += f"[v_concat_cfr]format=yuv420p,setsar=1[v_pre_quality];"

    if sharpen:
        video += f"[v_pre_quality]{NITIDEZ_SUAVE_FILTRO},format=yuv420p,setsar=1[v_out_base];"
    else:
        video += f"[v_pre_quality]copy[v_out_base];"

    if ass_subtitles and os.path.exists(ass_subtitles):
        sub_path = escape_ffmpeg_filter_path(ass_subtitles)
        fonts_part = ""
        if fonts_dir and os.path.exists(fonts_dir):
            fonts_part = f":fontsdir='{escape_ffmpeg_filter_path(fonts_dir)}'"
        video += (
            f"[v_out_base]subtitles='{sub_path}'{fonts_part},"
            f"format=yuv420p,setsar=1[v_out];"
        )
    else:
        video += f"[v_out_base]copy[v_out];"

    if has_audio:
        audio = (
            f"[1:a]aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS[a_clip];"
            f"[a_clip]asplit=2[ahooksrc][abodysrc];"
            f"[ahooksrc]atrim=0:{hook_d:.3f},asetpts=PTS-STARTPTS[a_hook];"
            f"[abodysrc]atrim={body_ini:.3f}:{duracion_clip:.3f},asetpts=PTS-STARTPTS[a_body];"
            f"anullsrc=channel_layout=stereo:sample_rate=48000:d={DURACION_INTRO:.3f}[a_intro];"
        )

        if usar_cierre_twitch:
            audio += (
                f"anullsrc=channel_layout=stereo:sample_rate=48000:d={dur_outro:.3f}[a_outro];"
                f"[a_hook][a_intro][a_body][a_outro]concat=n=4:v=0:a=1[a_raw];"
            )
        else:
            audio += f"[a_hook][a_intro][a_body]concat=n=3:v=0:a=1[a_raw];"

        if normalizar_audio:
            audio += f"[a_raw]loudnorm=I={audio_target_lufs}:TP=-1.5:LRA=11,alimiter=limit=0.95[a_out]"
        else:
            audio += "[a_raw]asetpts=PTS-STARTPTS[a_out]"
    else:
        audio = (
            f"anullsrc=channel_layout=stereo:sample_rate=48000:d={duracion_salida:.3f},"
            f"asetpts=PTS-STARTPTS[a_out]"
        )

    return video + audio


# ======================== PROCESAMIENTO ========================

def nombre_salida_clip(clip, indice):
    nombre_personalizado = clip.get("nombre_reel", "")

    if nombre_personalizado:
        nombre_base = limpiar_nombre_archivo(nombre_personalizado)

        if not nombre_base:
            nombre_base = f"reel_{NOMBRE_SESION}_clip{indice:02d}"

        return f"{nombre_base}_clip{indice:02d}.mp4"

    return f"reel_{NOMBRE_SESION}_clip{indice:02d}.mp4"


def construir_cmd_clip(stream, clip, nombre_salida, fps_salida, has_audio, encoder, preset_calidad, args=None):
    usar_cierre = not getattr(args, "no_outro", False)
    mostrar_barra = not getattr(args, "no_progress_bar", False)
    normalizar = (not getattr(args, "no_audio_normalize", False)) and (not getattr(args, "audio_two_pass", False))

    duracion_salida = clip["duracion"] + DURACION_INTRO

    if usar_cierre:
        duracion_salida += OUTRO_TWITCH_DURACION

    usar_logo_twitch_clip = os.path.exists(TWITCH_LOGO) and not getattr(args, "no_twitch_logo", False)
    usar_outro_bg = usar_cierre and os.path.exists(OUTRO_TWITCH_BG)

    entradas = [
        "-i", INTRO_PRE_RENDER,

        "-ss", str(clip["inicio"]),
        "-t", str(clip["duracion"]),
        "-i", stream,

        "-loop", "1",
        "-framerate", str(fps_salida),
        "-i", LOGO_CANAL
    ]

    siguiente_indice = 3
    twitch_idx = None
    outro_idx = None

    if usar_logo_twitch_clip:
        twitch_idx = siguiente_indice
        siguiente_indice += 1
        entradas += [
            "-loop", "1",
            "-framerate", str(fps_salida),
            "-i", TWITCH_LOGO
        ]

    if usar_outro_bg:
        outro_idx = siguiente_indice
        siguiente_indice += 1
        entradas += [
            "-loop", "1",
            "-framerate", str(fps_salida),
            "-t", str(OUTRO_TWITCH_DURACION),
            "-i", OUTRO_TWITCH_BG
        ]

    fg = filtergraph_unico(
        texto_gol=clip["texto_gol"],
        minuto_gol=clip["minuto_gol"],
        fps_salida=fps_salida,
        has_audio=has_audio,
        duracion_clip=clip["duracion"],
        hook_text=clip.get("hook_text", ""),
        subtitulo_visual=clip.get("subtitulo_visual", ""),
        mostrar_minuto=clip.get("mostrar_minuto", True),
        usar_cierre_twitch=usar_cierre,
        mostrar_barra=mostrar_barra,
        normalizar_audio=normalizar,
        ass_subtitles=clip.get("ass_subtitles"),
        fonts_dir=getattr(args, "fonts_dir", None),
        audio_target_lufs=float(getattr(args, "audio_target", -14.0)),
        usar_logo_twitch_clip=usar_logo_twitch_clip,
        engagement_question=clip.get("engagement_question") or getattr(args, "engagement_question", ""),
        comment_cta=clip.get("comment_cta") or getattr(args, "comment_cta", ""),
        sharpen=not getattr(args, "no_sharpen", False),
        twitch_logo_input_index=twitch_idx,
        outro_bg_input_index=outro_idx
    )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v", "error",
        "-progress", "pipe:2"
    ]

    cmd += entradas

    cmd += [
        "-filter_complex", fg,
        "-map", "[v_out]",
        "-map", "[a_out]"
    ]

    cmd += opciones_video_encoder(encoder, preset_calidad)
    cmd += opciones_keyframes(fps_salida, getattr(args, "no_keyframes", False), encoder)

    cmd += [
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", "48000",
        "-ac", "2",

        "-vsync", "cfr",
        "-r", str(fps_salida),

        "-t", str(duracion_salida),
        "-movflags", "+faststart",
        "-y",
        nombre_salida
    ]

    return cmd, duracion_salida


def procesar_clip(stream, clip, indice, carpeta, fps_salida, has_audio, encoder, preset_calidad, args=None):
    if args is not None and getattr(args, "_force_encoder", None):
        encoder = getattr(args, "_force_encoder")

    nombre_archivo = nombre_salida_clip(clip, indice)
    nombre = os.path.join(carpeta, nombre_archivo)

    os.makedirs(carpeta, exist_ok=True)

    usar_salida_segura = not getattr(args, "no_safe_output", False) if args is not None else True
    nombre_render = nombre
    if usar_salida_segura:
        base, ext = os.path.splitext(nombre)
        nombre_render = f"{base}.rendering.tmp{ext or '.mp4'}"
        try:
            if os.path.exists(nombre_render):
                os.remove(nombre_render)
        except OSError:
            pass

    print(f"  Etapa 1/2: preparación del clip #{indice:02d}")
    # Flujo manual: Whisper/subtítulos automáticos permanecen desactivados.

    print(f"  Etapa 2/2: render de video #{indice:02d}")
    timeout_seg = timeout_clip_segundos(clip.get("duracion", None), args)
    inactivity_timeout_seg = timeout_inactividad_segundos(args)

    def render_con_encoder(enc, descripcion):
        cmd, duracion_salida = construir_cmd_clip(
            stream=stream,
            clip=clip,
            nombre_salida=nombre_render,
            fps_salida=fps_salida,
            has_audio=has_audio,
            encoder=enc,
            preset_calidad=preset_calidad,
            args=args
        )

        elapsed = barra_progreso(
            cmd,
            descripcion=descripcion,
            duracion_seg=duracion_salida,
            timeout_seg=timeout_seg,
            inactivity_timeout_seg=inactivity_timeout_seg
        )
        return elapsed, duracion_salida

    try:
        elapsed, duracion_salida = render_con_encoder(encoder, f"Clip #{indice:02d}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if encoder != "libx264":
            print(f"⚠️ Falló encoder '{encoder}' o se quedó colgado. Reintentando con libx264...")
            if args is not None:
                setattr(args, "_force_encoder", "libx264")

            try:
                if os.path.exists(nombre_render):
                    os.remove(nombre_render)
            except OSError:
                pass

            elapsed, duracion_salida = render_con_encoder("libx264", f"Clip #{indice:02d} libx264")
        else:
            try:
                if os.path.exists(nombre_render):
                    os.remove(nombre_render)
            except OSError:
                pass
            raise

    if usar_salida_segura:
        if not os.path.exists(nombre_render) or os.path.getsize(nombre_render) <= 0:
            raise RuntimeError("FFmpeg terminó, pero no creó un archivo de salida válido.")
        os.replace(nombre_render, nombre)

    if args is not None and getattr(args, "audio_two_pass", False):
        print(f"  Validación de audio #{indice:02d}")
        try:
            normalizar_audio_two_pass_si_aplica(nombre, args)
        except Exception as e:
            print(f"⚠️ Loudnorm two-pass falló; se conserva el render original: {e}")

    return nombre, elapsed, duracion_salida


# ======================== INPUTS ========================

def seleccionar_video_interactivo():
    print("📂 Selecciona el video del stream/radio reacción...")

    root = Tk()
    root.withdraw()
    root.update()
    root.attributes("-topmost", True)
    root.lift()

    stream = filedialog.askopenfilename(
        parent=root,
        title="Selecciona el video del stream/radio reacción",
        filetypes=[
            ("Videos", "*.mp4 *.mkv *.mov *.avi *.webm"),
            ("Todos", "*.*")
        ]
    )

    root.attributes("-topmost", False)
    root.destroy()

    return stream


def pedir_clips(dur_stream):
    print("\n" + "─" * 70)
    print("  Marcación de tiempos del clip")
    print("  Formatos válidos: MM:SS, HH:MM:SS o segundos")
    print("  Tip: si dejas vacío el tiempo final, hará una prueba de 2 minutos")
    print("  Tip profesional: el hook debe ser corto, emocional y máximo de 2 líneas")
    print("─" * 70)

    clips = []
    n = 1

    while True:
        print(f"\n  ── Fragmento #{n} ──────────────────────────────")

        ini_str = input("  Tiempo de Inicio o Enter para terminar: ").strip()

        if ini_str.lower() in ("listo", "q", ""):
            break

        ini = tiempo_a_segundos(ini_str)

        if ini is None:
            print("  ❌ Formato inválido, prueba con MM:SS.")
            continue

        fin_str = input("  Tiempo de Fin o Enter para prueba de 2 minutos: ").strip()

        if fin_str == "":
            fin = ini + 120
        else:
            fin = tiempo_a_segundos(fin_str)

        if fin is None or fin <= ini:
            print("  ❌ Tiempo final inválido o menor que el inicio.")
            continue

        if dur_stream and fin > dur_stream:
            print(f"  ❌ Supera la duración total del stream ({segundos_a_tiempo(dur_stream)}).")
            continue

        dur = fin - ini

        if dur > ALERTA_DURACION_REDES:
            print(f"  ⚠️ Este fragmento dura {dur:.1f}s.")
            print("     Para TikTok/Reels/Shorts suele convertir mejor entre 30 y 50 segundos.")
            seguir = respuesta_si_no("¿Continuar con esta duración?", default=True)
            if not seguir:
                continue

        print("\n  Datos del título para este fragmento")
        es_partido = respuesta_si_no("¿Vas a hablar del partido y quieres mostrar MIN?", default=True)
        texto_gol = input("  Título principal del fragmento: ").strip()
        if es_partido:
            minuto_gol = input("  Minuto o bloque del evento: ").strip()
        else:
            minuto_gol = ""

        if not texto_gol:
            texto_gol = "REACCIÓN"

        if es_partido and not minuto_gol:
            minuto_gol = "--"

        print("\n  Hook y texto visual tipo redes")
        hook_def = hook_por_defecto(texto_gol)
        hook_text = input(f"  Hook inicial [{hook_def}]: ").strip() or hook_def
        subtitulo_visual = input("  Subtítulo/frase visual opcional o Enter para omitir: ").strip()

        print("\n  Nombre del archivo")
        nombre_reel = input("  Nombre personalizado del reel o Enter para automático: ").strip()

        clip = {
            "inicio": ini,
            "fin": fin,
            "duracion": dur,
            "texto_gol": texto_gol,
            "minuto_gol": minuto_gol,
            "mostrar_minuto": es_partido,
            "hook_text": hook_text,
            "subtitulo_visual": subtitulo_visual,
            "nombre_reel": nombre_reel
        }

        clips.append(clip)

        print(
            f"  ✅ Añadido fragmento #{n}: "
            f"{segundos_a_tiempo(ini)} → {segundos_a_tiempo(fin)} ({dur:.1f}s)"
        )

        if es_partido:
            print(f"     Título: {texto_gol} — Min {minuto_gol}")
        else:
            print(f"     Título: {texto_gol}")
        print(f"     Hook  : {hook_text}")
        if subtitulo_visual:
            print(f"     Texto : {subtitulo_visual}")

        archivo_preview = nombre_salida_clip(clip, n)
        print(f"     Archivo: {archivo_preview}")

        n += 1

    return clips

def imprimir_cola(clips, calidad, encoder, fps_salida, datos_caratula=None):
    print("\n" + "─" * 70)
    print("  COLA DE PRODUCCIÓN")
    print(f"  Sesión       : {NOMBRE_SESION}")
    print(f"  Total videos : {len(clips)}")
    print(f"  Calidad      : {calidad}")
    print(f"  Encoder      : {encoder}")
    print(f"  FPS salida   : {fps_salida}")
    print(f"  Branding corto: {DURACION_INTRO:.1f}s")
    print(f"  Hook inicial  : {HOOK_DURACION_DEFECTO:.1f}s sobre video real")
    print(f"  Cierre Twitch : {OUTRO_TWITCH_DURACION:.1f}s")

    if datos_caratula:
        print(f"  Título intro : {datos_caratula.get('titulo', TITULO_CARATULA_DEFECTO)}")
        print(f"  Carátula     : {PLANTILLA}")
        print(f"  Twitch fijo  : {MENSAJE_TWITCH_FIJO}")

    if RECORTES_ACTIVOS and "marcador" in RECORTES_ACTIVOS:
        print(f"  Marcador     : calibrado {RECORTES_ACTIVOS['marcador']}")
    else:
        print("  Marcador     : fallback fijo arriba")

    print()

    total_salida = 0.0

    for i, clip in enumerate(clips, 1):
        archivo_preview = nombre_salida_clip(clip, i)
        duracion_salida = clip["duracion"] + DURACION_INTRO
        if USAR_CIERRE_TWITCH:
            duracion_salida += OUTRO_TWITCH_DURACION
        total_salida += duracion_salida

        print(
            f"  #{i:02d} | "
            f"{segundos_a_tiempo(clip['inicio'])} → {segundos_a_tiempo(clip['fin'])} | "
            f"Clip: {clip['duracion']:.1f}s | Salida: {duracion_salida:.1f}s"
        )
        if clip.get('mostrar_minuto', True):
            print(f"       Título : {clip['texto_gol']} — Min {clip['minuto_gol']}")
        else:
            print(f"       Título : {clip['texto_gol']}")
        print(f"       Hook   : {clip.get('hook_text', '')}")
        if clip.get("auto_score") is not None:
            print(f"       Auto   : score {clip.get('auto_score')} | tipo {clip.get('auto_tipo')}")
        if clip.get("subtitulo_visual"):
            print(f"       Texto  : {clip.get('subtitulo_visual')}")
        print(f"       Archivo: {archivo_preview}")

    print()
    print(f"  Duración total estimada de salidas: {segundos_a_tiempo(total_salida)}")
    print()


# ======================== ARGUMENTOS ========================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Creador de reels Línea 90Min v7.1: render seguro, autodetección rápida y watchdog integral."
    )

    parser.add_argument(
        "-i", "--input",
        default=None,
        help="Ruta del video de entrada. Si no se indica, se abre selector de archivo."
    )

    parser.add_argument(
        "--quality",
        choices=list(PRESETS_CALIDAD.keys()),
        default=CALIDAD_DEFECTO,
        help="Preset de calidad."
    )

    parser.add_argument(
        "--platform",
        choices=list(PLATFORM_PRESETS.keys()),
        default="generic",
        help="Preset por plataforma sin cambiar layout: generic/tiktok/instagram/shorts/master."
    )

    parser.add_argument(
        "--encoder",
        default=ENCODER_DEFECTO,
        help="smart/auto recomendado, hw, libx264, h264_amf, h264_nvenc, h264_qsv, h264_videotoolbox."
    )

    parser.add_argument(
        "--smart-defaults",
        action="store_true",
        help="Activa ajustes recomendados para este PC: encoder smart, sin normalización de audio y calibración omitida."
    )

    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="No pide Enter antes de renderizar ni al finalizar. Útil para ejecución automática."
    )

    parser.add_argument(
        "--cover-title",
        default=None,
        help="Título superior que aparecerá en la carátula / branding."
    )

    parser.add_argument(
        "--font-file",
        default=None,
        help="Ruta a fuente .ttf/.otf para drawtext. Recomendado: Anton/Montserrat ExtraBold."
    )

    parser.add_argument(
        "--fonts-dir",
        default=None,
        help="Carpeta de fuentes para subtítulos ASS."
    )

    parser.add_argument(
        "--subtitle-font-name",
        default="Arial",
        help="Nombre interno de fuente para ASS. Ej: Anton, Montserrat ExtraBold, Arial."
    )

    parser.add_argument(
        "--no-outro",
        action="store_true",
        help="No agrega la pantalla final corta de Twitch."
    )

    parser.add_argument(
        "--no-progress-bar",
        action="store_true",
        help="No agrega la barra de progreso superior."
    )

    parser.add_argument(
        "--no-twitch-logo",
        action="store_true",
        help="No usa logo_twitch.png dentro del clip/outro; usa texto TWITCH como fallback."
    )

    parser.add_argument(
        "--engagement-question",
        default="",
        help="Pregunta corta para provocar comentarios. Ej: ¿CR7 titular o suplente?"
    )

    parser.add_argument(
        "--comment-cta",
        default="",
        help="CTA corto en pantalla. Ej: COMENTA TITULAR O SUPLENTE"
    )

    parser.add_argument(
        "--no-sharpen",
        action="store_true",
        help="Desactiva nitidez suave posterior al escalado."
    )

    parser.add_argument(
        "--no-audio-normalize",
        action="store_true",
        help="No aplica loudnorm/limitador al audio."
    )

    parser.add_argument(
        "--audio-two-pass",
        action="store_true",
        help="Aplica loudnorm two-pass al archivo final y genera reporte JSON."
    )

    parser.add_argument(
        "--audio-target",
        type=float,
        default=None,
        help="Target LUFS. Si no se indica, usa el preset de plataforma (-14 recomendado redes)."
    )

    parser.add_argument(
        "--audio-tp",
        type=float,
        default=-1.5,
        help="True Peak target para loudnorm."
    )

    parser.add_argument(
        "--audio-lra",
        type=float,
        default=11.0,
        help="LRA target para loudnorm."
    )

    parser.add_argument(
        "--auto-detect",
        action="store_true",
        help="Detecta clips automáticamente usando solo picos de audio."
    )

    parser.add_argument(
        "--auto-max-clips",
        type=int,
        default=10,
        help="Máximo de clips automáticos sugeridos."
    )

    parser.add_argument(
        "--auto-pre-roll",
        type=float,
        default=8.0,
        help="Segundos antes del evento automático."
    )

    parser.add_argument(
        "--auto-post-roll",
        type=float,
        default=32.0,
        help="Segundos después del evento automático."
    )

    parser.add_argument(
        "--auto-min-gap",
        type=float,
        default=20.0,
        help="Separación mínima entre eventos automáticos."
    )

    parser.add_argument(
        "--auto-threshold-percentile",
        type=float,
        default=90.0,
        help="Percentil RMS para detectar picos de audio. 90-95 suele funcionar."
    )

    parser.add_argument(
        "--no-keyframes",
        action="store_true",
        help="Desactiva GOP/keyframes cada 2 segundos."
    )

    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Muestra la cola de producción, pero no renderiza."
    )

    parser.add_argument(
        "--output-dir",
        default=CARPETA_SALIDA,
        help="Carpeta de salida."
    )

    parser.add_argument(
        "--skip-marker-calibration",
        action="store_true",
        help="No pregunta por calibración del marcador y usa el fallback fijo."
    )

    parser.add_argument(
        "--recalibrate-marker",
        action="store_true",
        help="Ignora el JSON guardado y fuerza nueva calibración del marcador."
    )

    parser.add_argument(
        "--clip-timeout-min",
        type=float,
        default=8.0,
        help="Minutos máximos por clip antes de cancelar FFmpeg y continuar. Evita cuelgues en 94%%-99%%."
    )

    parser.add_argument(
        "--clip-timeout-multiplier",
        type=float,
        default=12.0,
        help="Multiplicador de duración del clip para calcular timeout total."
    )

    parser.add_argument(
        "--clip-inactivity-timeout-min",
        type=float,
        default=2.0,
        help="Minutos sin avance real de FFmpeg antes de cancelar el clip."
    )

    parser.add_argument(
        "--no-safe-output",
        action="store_true",
        help="Desactiva salida temporal segura. Por defecto renderiza a .rendering.tmp.mp4 y luego reemplaza el final."
    )

    return parser.parse_args()


# ======================== MAIN ========================

def main():
    args = parse_args()

    if getattr(args, "smart_defaults", False):
        # Perfil recomendado para equipo AMD/Radeon + Ryzen, evitando GPU falsa.
        args.encoder = "smart"
        args.skip_marker_calibration = True
        args.no_audio_normalize = True
        if not args.platform or args.platform == "generic":
            args.platform = "tiktok"
        print("🧠 Smart defaults activos: encoder smart + autodetección por audio + sin normalización.")

    verificar_binarios()

    calidad = args.quality
    preset_calidad_base = PRESETS_CALIDAD[calidad]
    preset_calidad, preset_plataforma = aplicar_preset_plataforma(args, preset_calidad_base)
    if args.audio_target is None:
        args.audio_target = float(preset_plataforma.get("audio_target", -14.0))
    encoder = elegir_encoder(args.encoder)
    configurar_fuente(args.font_file)

    print("=" * 70)
    print("   CREADOR DE REELS - Línea 90Min v7.4 ESTABLE")
    print("   Lee logo_canal.png, logo_twitch.png, plantilla_intro.png y plantilla_outro.png")
    print("   Layout repartido según croquis + render seguro + watchdog FFmpeg + flujo manual")
    print("=" * 70 + "\n")

    if not os.path.exists(PLANTILLA):
        print(f"❌ No se encuentra la plantilla de intro '{PLANTILLA}' en esta carpeta.")
        print("   Asegúrate de que el archivo se llame exactamente: plantilla_intro.png")
        input("\nPresiona Enter para salir...")
        sys.exit(1)

    if not os.path.exists(OUTRO_TWITCH_BG):
        print(f"⚠️ No se encuentra la plantilla de outro '{OUTRO_TWITCH_BG}'.")
        print("   Asegúrate de que el archivo se llame: plantilla_outro.png")
        print("   Si no está, el script usará un outro generado básico.\n")

    if not os.path.exists(LOGO_CANAL):
        print(f"❌ No se encuentra el logo '{LOGO_CANAL}' en esta carpeta.")
        print("   Asegúrate de que el archivo se llame exactamente: logo_canal.png")
        input("\nPresiona Enter para salir...")
        sys.exit(1)

    stream = args.input

    if not stream:
        stream = seleccionar_video_interactivo()

    if not stream:
        print("❌ No seleccionaste ningún archivo.")
        sys.exit(1)

    if not os.path.exists(stream):
        print(f"❌ No existe el archivo: {stream}")
        sys.exit(1)

    info = obtener_info(stream)
    fps_salida = elegir_fps(info)

    imprimir_info_media(info, fps_salida)

    print("⚙️ Configuración de render:")
    print(f"   Calidad : {calidad} - {preset_calidad['uso']}")
    print(f"   CRF     : {preset_calidad['crf']}")
    print(f"   Preset  : {preset_calidad['preset']}")
    print(f"   Maxrate : {preset_calidad['maxrate']}")
    print(f"   Encoder : {encoder}")
    print(f"   Plataforma: {args.platform} - {preset_plataforma['descripcion']}")
    print(f"   Audio target: {args.audio_target} LUFS")
    print(f"   Escalado: {SCALING_ALGO}")
    print(f"   Layout   : top {H_TOP_PAD}px | marcador {H_MARCADOR}px | gap marcador {H_GAP_MARCADOR}px | cámaras {H_CAMARA}px | inferior {H_TEXTO_INF}px")
    print(f"   Nitidez  : {'no' if args.no_sharpen else 'suave para fuente 720p'}")
    print(f"   Intro asset : {PLANTILLA}")
    print(f"   Outro asset : {OUTRO_TWITCH_BG if os.path.exists(OUTRO_TWITCH_BG) else 'fallback generado'}")
    print(f"   Twitch logo: {'no' if args.no_twitch_logo else ('sí' if os.path.exists(TWITCH_LOGO) else 'no encontrado')}")
    print(f"   Keyframes: {'no' if args.no_keyframes else 'cada 2s'}")
    print("   Subtítulos IA: desactivados (flujo manual)")
    print(f"   Auto-detect por audio: {'sí' if args.auto_detect else 'no'}")
    print()

    datos_caratula = pedir_datos_caratula(args)

    if args.skip_marker_calibration:
        print("⚠️ Calibración de marcador omitida. Se usará el marcador fijo antiguo.")
    else:
        calibrar_recorte_marcador(
            stream,
            info.duracion,
            force_recalibrate=args.recalibrate_marker
        )

        if RECORTES_ACTIVOS and "marcador" in RECORTES_ACTIVOS:
            print("\n✅ Recorte activo del marcador:")
            print(f"   Marcador: {RECORTES_ACTIVOS['marcador']}")
        else:
            print("\n⚠️ No hay recorte calibrado. Se usará el marcador fijo de arriba.")

    print()

    if not args.plan_only:
        pre_renderizar_intro(fps_salida, preset_calidad, datos_caratula)
    else:
        print("⚠️ plan-only activo: la intro personalizada no se generará en este modo.\n")

    if args.auto_detect:
        clips = detectar_clips_automaticos(stream, info, args)
        if not clips:
            print("⚠️ Auto-detect no generó clips. Entrando a marcación manual.")
            clips = pedir_clips(info.duracion)
    else:
        clips = pedir_clips(info.duracion)

    if not clips:
        print("\n❌ No agregaste marcas de tiempo para recortar.")
        input("Presiona Enter para salir...")
        sys.exit(0)

    imprimir_cola(clips, calidad, encoder, fps_salida, datos_caratula)

    if args.plan_only:
        print("✅ Modo plan-only: no se renderizó ningún video.")
        if not getattr(args, "yes", False):
            input("\nPresiona Enter para cerrar el programa...")
        sys.exit(0)

    if not getattr(args, "yes", False):
        input("  Presiona [Enter] para iniciar el procesamiento de FFmpeg...")

    os.makedirs(args.output_dir, exist_ok=True)

    generados = []
    tiempo_total_render = 0.0

    for i, clip in enumerate(clips, 1):
        print(f"\n─ Exportando video #{i:02d} de {len(clips)} " + "─" * 40)

        try:
            ruta, elapsed, duracion_salida = procesar_clip(
                stream=stream,
                clip=clip,
                indice=i,
                carpeta=args.output_dir,
                fps_salida=fps_salida,
                has_audio=info.has_audio,
                encoder=encoder,
                preset_calidad=preset_calidad,
                args=args
            )

            generados.append(ruta)
            tiempo_total_render += elapsed

            size_mb = os.path.getsize(ruta) / 1_048_576

            velocidad = ""

            if elapsed > 0:
                velocidad = f" | Velocidad: {duracion_salida / elapsed:.2f}x"

            print(f"  ✅ Completado: {os.path.basename(ruta)} ({size_mb:.1f} MB){velocidad}")

            if getattr(args, "_force_encoder", None) and encoder != getattr(args, "_force_encoder"):
                encoder = getattr(args, "_force_encoder")
                print(f"  ℹ️ Encoder fijado para clips restantes: {encoder}")

        except Exception as e:
            print(f"  ❌ Error en clip #{i} — {e}")
            if encoder != "libx264":
                print("  ⚠️ Cambio definitivo a libx264 para los clips restantes.")
                encoder = "libx264"

    print("\n" + "=" * 70)
    print(f"  PROCESO FINALIZADO: {len(generados)}/{len(clips)} Reels listos")

    if generados:
        print(f"  Ubicación de guardado: {os.path.abspath(args.output_dir)}/")
        print(f"  Tiempo total render   : {tiempo_total_render:.1f}s")

        for f in generados:
            print(f"    🎬 → {os.path.basename(f)}")

    print("=" * 70)

    if not getattr(args, "yes", False):
        input("\nPresiona Enter para cerrar el programa...")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelado por el usuario.")
        sys.exit(130)