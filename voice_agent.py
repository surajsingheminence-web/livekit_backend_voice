import asyncio
import json
import logging
import os
import redis.asyncio as redis
from typing import Literal

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobProcess, cli, function_tool, metrics
from livekit.agents.voice import MetricsCollectedEvent
from livekit.plugins import google, openai, silero

# Import all the cleanly separated prompts
from prompt import (
    NANNY_AGENT_PROMPT,
    GENERAL_AGENT_PROMPT,
    MEAL_AGENT_PROMPT,
    LEARNING_AGENT_PROMPT,
    ACTIVITY_AGENT_PROMPT,
    PARENT_GUIDANCE_AGENT_PROMPT,
    HEALTH_WELLNESS_AGENT_PROMPT
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("todemy-voice-agent")

VOICE_SYSTEM_MODE = os.getenv("VOICE_SYSTEM_MODE", "multi").strip().lower()
LIVEKIT_AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "todemy-voice-agent")

OPENAI_LLM_MODEL = os.getenv("OPENAI_LLM_MODEL", "gpt-4o-mini")

# ==========================================
# REDIS MEMORY CONFIGURATION
# ==========================================
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
SESSION_TTL = 3600 

async def get_session_state(redis_client, room_name: str) -> dict:
    """Fetches the current structured memory for this specific call."""
    data = await redis_client.get(f"todemy:session:{room_name}")
    return json.loads(data) if data else {}

async def update_session_state(redis_client, room_name: str, key: str, value: str):
    """Updates the memory and resets the auto-delete timer."""
    state = await get_session_state(redis_client, room_name)
    state[key] = value
    await redis_client.setex(
        f"todemy:session:{room_name}", 
        SESSION_TTL, 
        json.dumps(state)
    )

# async def inject_memory_ctx(agent: Agent, room_name: str, redis_client):
#     """Copies chat history and safely injects the live Redis state."""
#     if agent.chat_ctx is None:
#         return None
    
#     ctx = agent.chat_ctx.copy(exclude_instructions=True)
#     state = await get_session_state(redis_client, room_name)
    
#     if state:
#         # Secretly inform the new agent about the user's saved preferences
#         ctx.messages.append(ChatMessage(
#             role="system", 
#             content=f"CURRENT USER CONTEXT (Do not read aloud): {json.dumps(state)}"
#         ))
#     return ctx

async def inject_memory_ctx(agent: Agent, room_name: str, redis_client):
    """Copies chat history and safely injects the live Redis state."""
    if agent.chat_ctx is None:
        return None
    
    ctx = agent.chat_ctx.copy(exclude_instructions=True)
    state = await get_session_state(redis_client, room_name)
    
    if state:
        ctx.add_message(
            role="system",
            content=f"CURRENT USER CONTEXT (Do not read aloud): {json.dumps(state)}"
        )
        
    return ctx


# ==========================================
# BASE AGENT WITH CONSTRAINED MEMORY TOOLS
# ==========================================
class MemoryAgent(Agent):
    """A base class passing the isolated Redis client directly to the agent."""
    def __init__(self, room_name: str, redis_client, instructions: str, chat_ctx=None):
        self.room_name = room_name
        self.redis_client = redis_client
        super().__init__(instructions=instructions, chat_ctx=chat_ctx)

    @function_tool()
    async def save_to_memory(
        self, 
        category: Literal[
            "dietary_preference", 
            "allergies", 
            "learning_goal", 
            "activity_preference", 
            "behavioral_issue", 
            "sleep_schedule",
            "general_child_info"
        ], 
        detail: str
    ):
        """
        Use this to save important details about the child so other agents remember them.
        """
        logger.info(f"Saving to Redis Memory -> {category}: {detail}")
        await update_session_state(self.redis_client, self.room_name, category, detail)
        return f"Successfully saved {detail} under {category}."


# ==========================================
# THE AGENT CLASSES
# ==========================================
class SingleNannyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=NANNY_AGENT_PROMPT)

class GeneralAgent(MemoryAgent):
    def __init__(self, room_name: str, redis_client, chat_ctx=None) -> None:
        super().__init__(room_name=room_name, redis_client=redis_client, instructions=GENERAL_AGENT_PROMPT, chat_ctx=chat_ctx)

    @function_tool()
    async def transfer_to_meal_agent(self):
        """Use when the user asks about meals, diet, snacks, picky eating, or nutrition."""
        ctx = await inject_memory_ctx(self, self.room_name, self.redis_client)
        return MealAgent(room_name=self.room_name, redis_client=self.redis_client, chat_ctx=ctx), "I can help with a meal plan! First, do you prefer vegetarian, non-veg, eggitarian, or vegan? And are there any specific ingredients you want to include or avoid?"

    @function_tool()
    async def transfer_to_activity_agent(self):
        """Use when the user asks for activity suggestions or planning."""
        ctx = await inject_memory_ctx(self, self.room_name, self.redis_client)
        return ActivityAgent(room_name=self.room_name, redis_client=self.redis_client, chat_ctx=ctx), "I would love to help plan an activity! Are you looking for something indoor or outdoor, how much time do you have, and is there a specific theme?"

    @function_tool()
    async def transfer_to_health_wellness_agent(self):
        """Use when the user asks about sleep, hygiene, or general non-emergency wellbeing."""
        ctx = await inject_memory_ctx(self, self.room_name, self.redis_client)
        return HealthWellnessAgent(room_name=self.room_name, redis_client=self.redis_client, chat_ctx=ctx), "I can help with general health and wellness. Could you share a few more details about what you are looking to address?"

    @function_tool()
    async def transfer_to_learning_agent(self):
        """Use when the user asks about educational activities, milestones, or learning."""
        ctx = await inject_memory_ctx(self, self.room_name, self.redis_client)
        return LearningAgent(room_name=self.room_name, redis_client=self.redis_client, chat_ctx=ctx), "Let us plan some learning! Do you have a specific theme in mind today, and how much available time do you have?"

    @function_tool()
    async def transfer_to_parent_guidance_agent(self):
        """Use when the user asks about discipline, tantrums, or emotional support for parents."""
        ctx = await inject_memory_ctx(self, self.room_name, self.redis_client)
        return ParentGuidanceAgent(room_name=self.room_name, redis_client=self.redis_client, chat_ctx=ctx), "I am here to support you. Could you tell me a little more about the specific parenting situation you want guidance on?"


# --- WORKER AGENTS ---
class MealAgent(MemoryAgent):
    def __init__(self, room_name: str, redis_client, chat_ctx=None) -> None:
        super().__init__(room_name=room_name, redis_client=redis_client, instructions=MEAL_AGENT_PROMPT, chat_ctx=chat_ctx)

    @function_tool()
    async def transfer_to_general_agent(self):
        """Use this ONLY when the user asks a question completely unrelated to meals, food, or nutrition."""
        ctx = await inject_memory_ctx(self, self.room_name, self.redis_client)
        return GeneralAgent(room_name=self.room_name, redis_client=self.redis_client, chat_ctx=ctx), "Let's look into that."

class ActivityAgent(MemoryAgent):
    def __init__(self, room_name: str, redis_client, chat_ctx=None) -> None:
        super().__init__(room_name=room_name, redis_client=redis_client, instructions=ACTIVITY_AGENT_PROMPT, chat_ctx=chat_ctx)
        
    @function_tool()
    async def transfer_to_general_agent(self):
        """Use this ONLY when the user asks a question completely unrelated to play or activities."""
        ctx = await inject_memory_ctx(self, self.room_name, self.redis_client)
        return GeneralAgent(room_name=self.room_name, redis_client=self.redis_client, chat_ctx=ctx), "Let's look into that."

class HealthWellnessAgent(MemoryAgent):
    def __init__(self, room_name: str, redis_client, chat_ctx=None) -> None:
        super().__init__(room_name=room_name, redis_client=redis_client, instructions=HEALTH_WELLNESS_AGENT_PROMPT, chat_ctx=chat_ctx)
        
    @function_tool()
    async def transfer_to_general_agent(self):
        """Use this ONLY when the user asks a question completely unrelated to health, wellness, or sleep."""
        ctx = await inject_memory_ctx(self, self.room_name, self.redis_client)
        return GeneralAgent(room_name=self.room_name, redis_client=self.redis_client, chat_ctx=ctx), "Let's look into that."

class LearningAgent(MemoryAgent):
    def __init__(self, room_name: str, redis_client, chat_ctx=None) -> None:
        super().__init__(room_name=room_name, redis_client=redis_client, instructions=LEARNING_AGENT_PROMPT, chat_ctx=chat_ctx)
        
    @function_tool()
    async def transfer_to_general_agent(self):
        """Use this ONLY when the user asks a question completely unrelated to education or learning."""
        ctx = await inject_memory_ctx(self, self.room_name, self.redis_client)
        return GeneralAgent(room_name=self.room_name, redis_client=self.redis_client, chat_ctx=ctx), "Let's look into that."

class ParentGuidanceAgent(MemoryAgent):
    def __init__(self, room_name: str, redis_client, chat_ctx=None) -> None:
        super().__init__(room_name=room_name, redis_client=redis_client, instructions=PARENT_GUIDANCE_AGENT_PROMPT, chat_ctx=chat_ctx)
        
    @function_tool()
    async def transfer_to_general_agent(self):
        """Use this ONLY when the user asks a question completely unrelated to parenting behavior or discipline."""
        ctx = await inject_memory_ctx(self, self.room_name, self.redis_client)
        return GeneralAgent(room_name=self.room_name, redis_client=self.redis_client, chat_ctx=ctx), "Let's look into that."


# ==========================================
# SERVER & ORCHESTRATION
# ==========================================
def create_root_agent(mode: str, room_name: str, redis_client) -> Agent:
    if mode == "single": return SingleNannyAgent()
    if mode == "multi": return GeneralAgent(room_name=room_name, redis_client=redis_client)
    raise ValueError("Invalid mode")

server = AgentServer()

def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(min_speech_duration=0.3, min_silence_duration=0.5)

server.setup_fnc = prewarm

@server.rtc_session(agent_name=LIVEKIT_AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    mode = VOICE_SYSTEM_MODE
    if ctx.job.metadata:
        try:
            payload = json.loads(ctx.job.metadata)
            mode = payload.get("mode", VOICE_SYSTEM_MODE).strip().lower()
        except: pass

    room_name = ctx.room.name

    # if mode == "single":
    #     selected_voice = os.getenv("GOOGLE_TTS_VOICE_KID", "en-IN-Wavenet-A")
    # else:
    #     selected_voice = os.getenv("GOOGLE_TTS_VOICE_PARENT", "en-IN-Wavenet-C")


    if mode == "single":
            # Nanny Mode: You can use a female Chirp HD voice here (e.g., 'F' or 'O')
            selected_voice = os.getenv("GOOGLE_TTS_VOICE_KID", "en-IN-Chirp-HD-F")
    else:
        # Multi-Agent Mode: The exact premium male voice you selected
        selected_voice = os.getenv("GOOGLE_TTS_VOICE_PARENT", "en-IN-Chirp-HD-D")   
    stt = openai.STT(model='gpt-4o-mini-transcribe', language='en')
    llm = openai.LLM(model=OPENAI_LLM_MODEL, temperature=0.6)
    # tts = google.TTS(voice_name=selected_voice, language="en-IN")
    tts = google.TTS(voice_name=selected_voice, language="en-IN")

    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    # ==========================================
    # METRICS & GRACEFUL REDIS TEARDOWN
    # ==========================================
    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    # Safely initialize Redis specifically for this session loop
    session_redis = redis.from_url(REDIS_URL, decode_responses=True)

    async def log_and_cleanup():
        # Safely shut down the Redis connection pool to prevent loop leaks
        await session_redis.aclose()
        summary = usage_collector.get_summary()
        logger.info(f"Session Usage Summary: {summary}")

    ctx.add_shutdown_callback(log_and_cleanup)
    # ==========================================

    await session_redis.setex(f"todemy:session:{room_name}", SESSION_TTL, json.dumps({}))

    await session.start(agent=create_root_agent(mode, room_name, session_redis), room=ctx.room)
    await ctx.connect()

if __name__ == "__main__":
    cli.run_app(server)
