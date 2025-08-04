from langgraph.graph import END
from typing_extensions import Literal

from ai_companion.graph.state import AICompanionState
from ai_companion.settings import settings

import os
import logging

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


def should_summarize_conversation(
    state: AICompanionState,
) -> Literal["summarize_conversation_node", "__end__"]:
    messages = state["messages"]

    if len(messages) > settings.TOTAL_MESSAGES_SUMMARY_TRIGGER:
        return "summarize_conversation_node"

    return END


def select_workflow(
    state: AICompanionState,
) -> Literal["conversation_node", "image_node", "audio_node", "call_tool"]:
    """
     Determines the next step in the workflow based on the agent's decision.
     Now also checks for tool calls.
     """
    
    logger.info(f"SelectWorkflow: Estado actual del workflow: {state.get('workflow')}")

    #workflow = state["workflow"]
    workflow = state.get("workflow", "conversation")

    if workflow == "conversation":
         return "conversation" # return "conversation_node"
    elif workflow == "image":
        return "image" # return "image_node"
    elif workflow == "audio":
        return "audio" # return "audio_node"
    
    # Como fallback final, si algo sale muy mal
    logger.warning(f"SelectWorkflow: Valor de workflow inesperado. Retornando 'conversation' por defecto.")
    return "conversation" # return "conversation_node"
