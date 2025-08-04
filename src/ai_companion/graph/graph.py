import os
import logging

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from ai_companion.graph.edges import (
    select_workflow,
    should_summarize_conversation,
)
from ai_companion.graph.nodes import (
    audio_node,
    context_injection_node,
    conversation_node,
    image_node,
    memory_extraction_node,
    memory_injection_node,
    router_node,
    summarize_conversation_node,
)
from ai_companion.graph.state import AICompanionState

# from ai_companion.graph.utils.tools import create_or_update_kommo_lead
from langchain_core.tools import Tool # Aunque ya la usamos en chains.py, la puedes necesitar aquí si pasas las tools a ToolNode directamente

# Importa la tool de Tools.py para pasarla al ToolNode
from ai_companion.graph.utils.tools import create_or_update_kommo_lead

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

# @lru_cache(maxsize=1)
def create_workflow_graph():
    graph_builder = StateGraph(AICompanionState)

    # Define las herramientas que el ToolNode ejecutará
    # Asegúrate de que esta lista de Tools sea la misma que le bindeas al LLM
    tools_for_toolnode = [
        # Tool(
        #     name="create_or_update_kommo_lead",
        #     description="Crea o actualiza un lead en Kommo basado en el progreso del usuario.",
        #     func=create_or_update_kommo_lead
        # )
    ]
    # Agrega el ToolNode al grafo
    graph_builder.add_node("call_tool", ToolNode(tools_for_toolnode))
    logger.info(f"Agrega el ToolNode al grafo con las herramientas: {tools_for_toolnode} para la ejecución de acciones.")

    # Add all nodes
    graph_builder.add_node("memory_extraction_node", memory_extraction_node)
    graph_builder.add_node("router_node", router_node)
    graph_builder.add_node("context_injection_node", context_injection_node)
    graph_builder.add_node("memory_injection_node", memory_injection_node)
    graph_builder.add_node("conversation_node", conversation_node)
    logger.info(f"Agrega los nodos al grafo: memory_extraction_node, router_node, context_injection_node, memory_injection_node, conversation_node.")
    graph_builder.add_node("image_node", image_node)
    graph_builder.add_node("audio_node", audio_node)
    graph_builder.add_node("summarize_conversation_node", summarize_conversation_node)

    # Define the flow
    # First extract memories from user message
    graph_builder.add_edge(START, "memory_extraction_node")

    # Then determine response type
    graph_builder.add_edge("memory_extraction_node", "router_node")

    # Then inject both context and memories
    graph_builder.add_edge("router_node", "context_injection_node")
    graph_builder.add_edge("context_injection_node", "memory_injection_node")

    graph_builder.add_conditional_edges(
        "memory_injection_node",
        select_workflow, # select_workflow DEBE ahora decidir si ir a 'conversation', 'image', 'audio' O 'call_tool'
        {
            "conversation": "conversation_node",
            "image": "image_node",
            "audio": "audio_node",
            # "call_tool": "call_tool", # <--- Nuevo camino para la herramienta
        }
    )

    # Después de que la herramienta se ejecuta, el control vuelve al conversation_node
    # para que el agente pueda generar una respuesta textual confirmando la acción o continuando.
    # graph_builder.add_edge("call_tool", "conversation_node") # <--- Vuelve al agente para responder

    # Check for summarization after any response
    graph_builder.add_conditional_edges("conversation_node", should_summarize_conversation)
    graph_builder.add_conditional_edges("image_node", should_summarize_conversation)
    graph_builder.add_conditional_edges("audio_node", should_summarize_conversation)
    graph_builder.add_edge("summarize_conversation_node", END)

    logger.info(f"Tipo de conversation_node antes de compilar: {type(conversation_node)}")
    logger.info(f"Nombre de conversation_node antes de compilar: {conversation_node.__name__}")

    return graph_builder

# Compiled without a checkpointer. Used for LangGraph Studio
graph = create_workflow_graph().compile()
