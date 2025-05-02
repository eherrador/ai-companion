import logging
import os
from io import BytesIO
from typing import Dict

import httpx
from fastapi import APIRouter, Request, Response
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_core.messages import BaseMessage

from ai_companion.graph import graph_builder
from ai_companion.modules.image import ImageToText
from ai_companion.modules.speech import SpeechToText, TextToSpeech
from ai_companion.settings import settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Crear directorio de logs si no existe
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configurar el handler para archivo
file_handler = logging.FileHandler(os.path.join(log_dir, "whatsapp.log"))
file_handler.setLevel(logging.INFO)

# Configurar el formato del log
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

# Agregar el handler al logger
logger.addHandler(file_handler)

# Global module instances
speech_to_text = SpeechToText()
text_to_speech = TextToSpeech()
image_to_text = ImageToText()

# Router for WhatsApp respo
whatsapp_router = APIRouter()

# WhatsApp API credentials
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

logger.info(f"Token: {WHATSAPP_TOKEN}")
logger.info(f"Phone ID: {WHATSAPP_PHONE_NUMBER_ID}")
logger.info(f"DB Path: {settings.SHORT_TERM_MEMORY_DB_PATH}")

@whatsapp_router.get("/whatsapp_response")
async def verify_token(request: Request) -> Response:
    """Verifica el token de autenticación para el webhook de WhatsApp.

    Esta función maneja las solicitudes GET de verificación del webhook de WhatsApp.
    Es parte del proceso de configuración inicial del webhook y verifica que las solicitudes
    provengan de WhatsApp utilizando un token de verificación predefinido.

    Args:
        request (Request): El objeto Request de FastAPI que contiene los parámetros de la solicitud.

    Returns:
        Response: Un objeto Response con:
            - status_code 200: Si el token de verificación es correcto, incluye el challenge en el contenido
            - status_code 403: Si el token de verificación no coincide

    Notas:
        - El token de verificación se obtiene de la variable de entorno WHATSAPP_VERIFY_TOKEN
        - El challenge se obtiene del parámetro 'hub.challenge' de la URL
        - Esta verificación es un requisito de seguridad de la API de WhatsApp
    """
    params = request.query_params
    if params.get("hub.verify_token") == os.getenv("WHATSAPP_VERIFY_TOKEN"):
        return Response(content=params.get("hub.challenge"), status_code=200)
    return Response(content="Verification token mismatch", status_code=403)

@whatsapp_router.post("/whatsapp_response")
async def receive_message(request: Request) -> Response:
    """Maneja los mensajes entrantes y actualizaciones de estado de la API de WhatsApp.

    Esta función procesa las solicitudes POST del webhook de WhatsApp, manejando diferentes tipos de mensajes
    (texto, audio, imagen) y generando respuestas apropiadas a través del agente de IA.

    Args:
        request (Request): El objeto Request de FastAPI que contiene los datos de la solicitud.

    Returns:
        Response: Un objeto Response con:
            - status_code 200: Si el mensaje se procesó correctamente
            - status_code 400: Si el tipo de evento es desconocido
            - status_code 500: Si ocurrió un error interno o falló el envío de la respuesta

    Raises:
        Exception: Si ocurre cualquier error durante el procesamiento del mensaje.
            El error se registra y se devuelve un mensaje de error genérico al cliente.

    Notas:
        - Para mensajes de audio: Transcribe el audio a texto
        - Para mensajes de imagen: Analiza la imagen y genera una descripción
        - Para mensajes de texto: Procesa directamente el contenido
        - Utiliza un agente de IA para generar respuestas contextuales
        - Mantiene el estado de la conversación usando SQLite
    """
    try:
        data = await request.json()
        logging.info(f"Incoming data: {data}")

        change_value = data["entry"][0]["changes"][0]["value"]
        if "messages" in change_value:
            message = change_value["messages"][0]
            from_number = message["from"]
            if not from_number:
                logger.warning("No se encontró el número del remitente en el mensaje.")
                return Response(content="Missing sender number", status_code=400)
            
            from_number = normalize_phone_number(from_number)
            logger.info(f"Número recibido: {from_number}")
            session_id = from_number

            # Corregimos el número para agregar el "+" si falta
            if not from_number.startswith("+"):
                from_number = f"+{from_number}"

            logger.info(f"Enviando mensaje a: {from_number}")
            assert from_number.startswith("+")

            # Get user message and handle different message types
            content = ""
            if message["type"] == "audio":
                content = await process_audio_message(message)
            elif message["type"] == "image":
                # Get image caption if any
                content = message.get("image", {}).get("caption", "")
                # Download and analyze image
                image_bytes = await download_media(message["image"]["id"])
                try:
                    description = await image_to_text.analyze_image(
                        image_bytes,
                        "Please describe what you see in this image in the context of our conversation.",
                    )
                    content += f"\n[Image Analysis: {description}]"
                except Exception as e:
                    logger.warning(f"Failed to analyze image: {e}")
            else:
                content = message["text"]["body"]

            # Process message through the graph agent
            async with AsyncSqliteSaver.from_conn_string(settings.SHORT_TERM_MEMORY_DB_PATH) as short_term_memory:
                graph = graph_builder.compile(checkpointer=short_term_memory)
                human_message = HumanMessage(content=content)
                messages = deduplicate_messages([human_message])

                logger.debug(f"[Graph Input] session_id={session_id} | messages={[(m.__class__.__name__, m.content) for m in messages]}")

                await graph.ainvoke(
                    {"messages": messages},
                    {"configurable": {"thread_id": session_id}},
                )

                # Get the workflow type and response from the state
                output_state = await graph.aget_state(config={"configurable": {"thread_id": session_id}})

            workflow = output_state.values.get("workflow", "conversation")
            response_message = output_state.values["messages"][-1].content

            # Handle different response types based on workflow
            if workflow == "audio":
                audio_buffer = output_state.values["audio_buffer"]
                success = await send_response(from_number, response_message, "audio", audio_buffer)
            elif workflow == "image":
                image_path = output_state.values["image_path"]
                with open(image_path, "rb") as f:
                    image_data = f.read()
                success = await send_response(from_number, response_message, "image", image_data)
            else:
                success = await send_response(from_number, response_message, "text")

            if not success:
                return Response(content="Failed to send message", status_code=500)

            return Response(content="Message processed", status_code=200)

        elif "statuses" in change_value:
            return Response(content="Status update received", status_code=200)

        else:
            return Response(content="Unknown event type", status_code=400)

    except Exception as e:
        error_message = f"Internal server error: {str(e)}"
        logger.error(f"Error processing message: {e}", exc_info=True)
        return Response(content=error_message, status_code=500)


async def download_media(media_id: str) -> bytes:
    """Download media from WhatsApp."""
    media_metadata_url = f"https://graph.facebook.com/v22.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    async with httpx.AsyncClient() as client:
        metadata_response = await client.get(media_metadata_url, headers=headers)
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        download_url = metadata.get("url")

        media_response = await client.get(download_url, headers=headers)
        media_response.raise_for_status()
        return media_response.content


async def process_audio_message(message: Dict) -> str:
    """Download and transcribe audio message."""
    audio_id = message["audio"]["id"]
    media_metadata_url = f"https://graph.facebook.com/v22.0/{audio_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    async with httpx.AsyncClient() as client:
        metadata_response = await client.get(media_metadata_url, headers=headers)
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        download_url = metadata.get("url")

    # Download the audio file
    async with httpx.AsyncClient() as client:
        audio_response = await client.get(download_url, headers=headers)
        audio_response.raise_for_status()

    # Prepare for transcription
    audio_buffer = BytesIO(audio_response.content)
    audio_buffer.seek(0)
    audio_data = audio_buffer.read()

    return await speech_to_text.transcribe(audio_data)


async def send_response(
    from_number: str,
    response_text: str,
    message_type: str = "text",
    media_content: bytes = None,
) -> bool:
    """Send response to user via WhatsApp API."""
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    json_data = None

    if message_type in ["audio", "image"]:
        try:
            mime_type = "audio/mpeg" if message_type == "audio" else "image/png"
            media_buffer = BytesIO(media_content)
            media_id = await upload_media(media_buffer, mime_type)

            if not media_id:
                raise ValueError("Media ID is None, upload failed.")
            
            logger.info(f"Número de WhatsApp al que se enviará la respuesta : {from_number}")
            
            json_data = {
                "messaging_product": "whatsapp",
                "to": from_number,
                "type": message_type,
                message_type: {"id": media_id},
            }

            # Add caption for images
            if message_type == "image":
                json_data["image"]["caption"] = response_text
        except Exception as e:
            logger.error(f"Media upload failed, falling back to text: {e}")
            message_type = "text"

    if message_type == "text" or json_data is None:
        json_data = {
            "messaging_product": "whatsapp",
            "to": from_number,
            "type": "text",
            "text": {"body": response_text},
        }

    logger.debug(f"Headers: {headers}")
    logger.debug(f"Sending to WhatsApp: {json_data}")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}/messages",
            headers=headers,
            json=json_data,
        )

    if response.status_code != 200:
        logger.error(f"WhatsApp API Error: {response.status_code} - {response.text}")

    # Agregamos esto para más contexto
    logger.info(f"WhatsApp response status: {response.status_code}")
    logger.info(f"WhatsApp response body: {response.text}")

    return response.status_code == 200


async def upload_media(media_content: BytesIO, mime_type: str) -> str:
    """Upload media to WhatsApp servers."""
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    files = {"file": ("response.mp3", media_content, mime_type)}
    data = {"messaging_product": "whatsapp", "type": mime_type}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}/media",
            headers=headers,
            files=files,
            data=data,
        )
        result = response.json()

    if "id" not in result:
        raise Exception("Failed to upload media")
    return result["id"]

def deduplicate_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    seen = set()
    unique = []
    for msg in messages:
        key = (msg.__class__.__name__, msg.content)
        if key not in seen:
            unique.append(msg)
            seen.add(key)
    return unique

def normalize_phone_number(raw_number: str) -> str:
    """
    Convierte un número a formato E.164 para México sin el 1 extra.
    """
    if raw_number.startswith("521") and len(raw_number) == 13:
        # Número móvil de México malformado (con el 1 de larga distancia)
        return "+52" + raw_number[3:]
    elif raw_number.startswith("52") and len(raw_number) == 12:
        # Ya está bien
        return "+" + raw_number
    elif raw_number.startswith("+52") and len(raw_number) == 13:
        # También bien
        return raw_number
    else:
        raise ValueError(f"Número no válido: {raw_number}")