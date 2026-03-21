import asyncio
import json
import logging
import os

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobProcess, cli, function_tool, inference
from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics
from livekit.plugins import cartesia, openai, silero


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("todemy-voice-agent")


# Default fallback when no explicit dispatch metadata is provided.
VOICE_SYSTEM_MODE = os.getenv("VOICE_SYSTEM_MODE", "single").strip().lower()

LIVEKIT_AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "todemy-voice-agent")
AGENT_LANGUAGE = os.getenv("AGENT_LANGUAGE", "en")

OPENAI_STT_MODEL = os.getenv("OPENAI_STT_MODEL", "gpt-4o-transcribe")
OPENAI_LLM_MODEL = os.getenv("OPENAI_LLM_MODEL", "gpt-4.1-mini")
OPENAI_LLM_API_MODE = os.getenv("OPENAI_LLM_API_MODE", "chat_completions").strip().lower()

CARTESIA_TTS_MODEL = os.getenv("CARTESIA_TTS_MODEL", "sonic-3")
CARTESIA_VOICE_ID = os.getenv(
    "CARTESIA_VOICE_ID",
    "f786b574-daa5-4673-aa0c-cbe3e8534c02",
)
CARTESIA_TTS_SPEED = float(os.getenv("CARTESIA_TTS_SPEED", "0.92"))
TTS_PROVIDER_MODE = os.getenv("TTS_PROVIDER_MODE", "cartesia_plugin").strip().lower()


def copy_chat_ctx(agent: Agent):
    if agent.chat_ctx is None:
        return None
    return agent.chat_ctx.copy(exclude_instructions=True)


async def on_llm_metrics_collected(metrics: LLMMetrics) -> None:
    print("\n--- LLM Metrics ---")
    print(f"Prompt Tokens: {metrics.prompt_tokens}")
    print(f"Completion Tokens: {metrics.completion_tokens}")
    print(f"Total Tokens: {metrics.total_tokens}")
    print(f"Tokens per second: {metrics.tokens_per_second:.4f}")
    print(f"TTFT: {metrics.ttft:.4f}s")
    print("-------------------\n")


async def on_stt_metrics_collected(metrics: STTMetrics) -> None:
    print("\n--- STT Metrics ---")
    print(f"Duration: {metrics.duration:.4f}s")
    print(f"Audio Duration: {metrics.audio_duration:.4f}s")
    print(f"Streamed: {'Yes' if metrics.streamed else 'No'}")
    print("-------------------\n")


async def on_eou_metrics_collected(metrics: EOUMetrics) -> None:
    print("\n--- EOU Metrics ---")
    print(f"End of Utterance Delay: {metrics.end_of_utterance_delay:.4f}s")
    print(f"Transcription Delay: {metrics.transcription_delay:.4f}s")
    print("-------------------\n")


async def on_tts_metrics_collected(metrics: TTSMetrics) -> None:
    print("\n--- TTS Metrics ---")
    print(f"TTFB: {metrics.ttfb:.4f}s")
    print(f"Duration: {metrics.duration:.4f}s")
    print(f"Audio Duration: {metrics.audio_duration:.4f}s")
    print(f"Streamed: {'Yes' if metrics.streamed else 'No'}")
    print("-------------------\n")


def attach_metrics(stt, llm, tts: cartesia.TTS) -> None:
    def llm_metrics_wrapper(metrics: LLMMetrics) -> None:
        asyncio.create_task(on_llm_metrics_collected(metrics))

    def stt_metrics_wrapper(metrics: STTMetrics) -> None:
        asyncio.create_task(on_stt_metrics_collected(metrics))

    def eou_metrics_wrapper(metrics: EOUMetrics) -> None:
        asyncio.create_task(on_eou_metrics_collected(metrics))

    def tts_metrics_wrapper(metrics: TTSMetrics) -> None:
        asyncio.create_task(on_tts_metrics_collected(metrics))

    llm.on("metrics_collected", llm_metrics_wrapper)
    stt.on("metrics_collected", stt_metrics_wrapper)
    stt.on("eou_metrics_collected", eou_metrics_wrapper)
    tts.on("metrics_collected", tts_metrics_wrapper)


class SingleNannyAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""
    You are a very gentle nanny-style voice agent for a child aged 2 to 5.

Your job is to sound warm, safe, calm, soft, and loving, like a caring parent.
Use short, simple, friendly sentences that a very young child can understand.
Keep your tone low, soothing, playful, and reassuring.
You can sing little rhymes, count, name colors, tell tiny stories, comfort the child,
or gently guide them into simple activities.
Keep most replies to 1 to 3 short sentences.

    Never sound strict, scary, intense, or overstimulating.
    Never use difficult words unless you explain them simply.
    If the child sounds upset, comfort them first.
    If the child asks for something unsafe, redirect them gently and encourage asking a grown-up.
    Do not claim to be the child's real parent. Instead, speak with the warmth and familiarity of one.
    """.strip()
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="""
            Greet the child very gently.
            Keep it short, loving, and easy to understand.
            Invite them to talk, play, or listen to a tiny story.
            """.strip()
                    )


class GeneralAgent(Agent):
    def __init__(self, chat_ctx=None) -> None:
        super().__init__(
            instructions="""
You are the general family support voice agent.

Handle greetings, gratitude, general questions, and follow-up conversation.
Be warm, concise, and helpful.
Keep voice replies brief unless the user asks for more detail.
When the user needs specialized help, transfer them to the correct expert agent:
- Meal Agent for meal plans, diet, snacks, picky eating, recipes, and nutrition.
- Activity Agent for play ideas, daily activities, outings, and planning.
- Learning Agent for study help, learning plans, developmental learning, and educational support.
- Parent Guidance Agent for parenting advice, emotional support, routines, and parent coaching.
- Health and Wellness Agent for health habits, sleep, wellness, and non-emergency wellbeing questions.
""".strip(),
            chat_ctx=chat_ctx,
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="""
Greet the user warmly.
Briefly explain that you can help directly or connect them to meal, activity, learning,
parent guidance, or health and wellness support.
""".strip()
        )

    @function_tool()
    async def transfer_to_meal_agent(self):
        """Use this when the user asks about meals, diet, snacks, picky eating, recipes, or nutrition."""
        return MealAgent(chat_ctx=copy_chat_ctx(self))

    @function_tool()
    async def transfer_to_activity_agent(self):
        """Use this when the user asks for activity suggestions, planning, play ideas, or outing ideas."""
        return ActivityAgent(chat_ctx=copy_chat_ctx(self))

    @function_tool()
    async def transfer_to_learning_agent(self):
        """Use this when the user asks about teaching, learning plans, educational help, or study support."""
        return LearningAgent(chat_ctx=copy_chat_ctx(self))

    @function_tool()
    async def transfer_to_parent_guidance_agent(self):
        """Use this when the user wants parenting advice, support, routines, or guidance for being a better parent."""
        return ParentGuidanceAgent(chat_ctx=copy_chat_ctx(self))

    @function_tool()
    async def transfer_to_health_wellness_agent(self):
        """Use this when the user asks about health, sleep, hygiene, wellbeing, or wellness-related concerns."""
        return HealthWellnessAgent(chat_ctx=copy_chat_ctx(self))


class MealAgent(Agent):
    def __init__(self, chat_ctx=None) -> None:
        super().__init__(
            instructions="""
You are the Meal Agent.

Help with meal plans, diet ideas, child-friendly meals, healthy snacks, grocery suggestions,
portion ideas, picky eating support, and simple nutrition guidance.
Be practical, kind, and easy to follow.
Keep voice replies concise, usually 2 to 4 short sentences or a short bullet-like spoken list.
Do not give emergency medical advice. If the user asks about serious symptoms, urgent concerns,
or diagnosis, hand back to the general agent so they can be guided appropriately.
""".strip(),
            chat_ctx=chat_ctx,
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Acknowledge the meal topic and offer calm, practical support."
        )

    @function_tool()
    async def transfer_to_general_agent(self):
        """Use this when the request is no longer about meals or nutrition."""
        return GeneralAgent(chat_ctx=copy_chat_ctx(self))


class ActivityAgent(Agent):
    def __init__(self, chat_ctx=None) -> None:
        super().__init__(
            instructions="""
You are the Activity Agent.

Help with indoor activities, outdoor play, creative ideas, sensory play, schedules,
weekend planning, low-prep games, and child-friendly engagement ideas.
Favor safe, age-appropriate, realistic suggestions.
Keep replies short and easy to speak out loud.
""".strip(),
            chat_ctx=chat_ctx,
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Acknowledge the activity topic and offer a few helpful ideas."
        )

    @function_tool()
    async def transfer_to_general_agent(self):
        """Use this when the request changes away from activities or planning."""
        return GeneralAgent(chat_ctx=copy_chat_ctx(self))


class LearningAgent(Agent):
    def __init__(self, chat_ctx=None) -> None:
        super().__init__(
            instructions="""
You are the Learning Agent.

Help with learning plans, educational activities, concept explanations, study support,
developmental learning ideas, and simple teaching strategies for children.
Be encouraging, clear, and step by step.
Keep replies short and structured for voice.
""".strip(),
            chat_ctx=chat_ctx,
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Acknowledge the learning topic and offer structured, supportive help."
        )

    @function_tool()
    async def transfer_to_general_agent(self):
        """Use this when the request is no longer about learning or educational support."""
        return GeneralAgent(chat_ctx=copy_chat_ctx(self))


class ParentGuidanceAgent(Agent):
    def __init__(self, chat_ctx=None) -> None:
        super().__init__(
            instructions="""
You are the Parent Guidance Agent.

Help parents with routines, emotional regulation, bonding, communication, discipline,
confidence, consistency, and calm parenting support.
Be compassionate, practical, and never judgmental.
Keep replies focused and concise for voice conversation.
""".strip(),
            chat_ctx=chat_ctx,
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Acknowledge the parenting question and respond with supportive, non-judgmental guidance."
        )

    @function_tool()
    async def transfer_to_general_agent(self):
        """Use this when the request is no longer about parenting support or guidance."""
        return GeneralAgent(chat_ctx=copy_chat_ctx(self))


class HealthWellnessAgent(Agent):
    def __init__(self, chat_ctx=None) -> None:
        super().__init__(
            instructions="""
You are the Health and Wellness Agent.

Help with general wellness, sleep habits, hygiene, hydration, stress reduction,
healthy routines, and non-emergency family wellbeing questions.
Be calm, careful, and practical.
Keep replies short and clear for spoken delivery.
Do not diagnose, prescribe, or present yourself as a medical professional.
For urgent or serious medical concerns, advise seeking qualified medical help promptly.
""".strip(),
            chat_ctx=chat_ctx,
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Acknowledge the health or wellness topic and offer general supportive guidance."
        )

    @function_tool()
    async def transfer_to_general_agent(self):
        """Use this when the request is no longer about health or wellness."""
        return GeneralAgent(chat_ctx=copy_chat_ctx(self))


def resolve_mode(job_metadata: str | None = None) -> str:
    if job_metadata:
        try:
            payload = json.loads(job_metadata)
        except json.JSONDecodeError:
            logger.warning("invalid job metadata JSON, using fallback mode")
        else:
            mode = str(payload.get("mode", "")).strip().lower()
            if mode in {"single", "multi"}:
                return mode

    return VOICE_SYSTEM_MODE


def create_root_agent(mode: str) -> Agent:
    if mode == "single":
        return SingleNannyAgent()
    if mode == "multi":
        return GeneralAgent()
    raise ValueError("mode must be either 'single' or 'multi'.")


def create_llm():
    if OPENAI_LLM_API_MODE == "chat_completions":
        return openai.LLM(
            model=OPENAI_LLM_MODEL,
            temperature=0.4,
        )

    if OPENAI_LLM_API_MODE == "responses_http":
        return openai.responses.LLM(
            model=OPENAI_LLM_MODEL,
            temperature=0.4,
            use_websocket=False,
        )

    if OPENAI_LLM_API_MODE == "responses_ws":
        return openai.responses.LLM(
            model=OPENAI_LLM_MODEL,
            temperature=0.4,
            use_websocket=True,
        )

    raise ValueError(
        "OPENAI_LLM_API_MODE must be one of: chat_completions, responses_http, responses_ws."
    )


def create_tts():
    if TTS_PROVIDER_MODE == "cartesia_plugin":
        return cartesia.TTS(
            model=CARTESIA_TTS_MODEL,
            voice=CARTESIA_VOICE_ID,
            language=AGENT_LANGUAGE,
            speed=CARTESIA_TTS_SPEED,
        )

    if TTS_PROVIDER_MODE == "livekit_inference":
        return inference.TTS(
            model=f"cartesia/{CARTESIA_TTS_MODEL}",
            voice=CARTESIA_VOICE_ID,
            language=AGENT_LANGUAGE,
            extra_kwargs={"speed": CARTESIA_TTS_SPEED},
        )

    raise ValueError(
        "TTS_PROVIDER_MODE must be one of: cartesia_plugin, livekit_inference."
    )


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name=LIVEKIT_AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    mode = resolve_mode(ctx.job.metadata)
    ctx.log_context_fields = {"mode": mode, "room": ctx.room.name}

    stt = openai.STT(
        model=OPENAI_STT_MODEL,
        language=AGENT_LANGUAGE,
    )
    llm = create_llm()
    tts = create_tts()

    attach_metrics(stt=stt, llm=llm, tts=tts)

    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    await session.start(
        agent=create_root_agent(mode),
        room=ctx.room,
    )

    await ctx.connect()


if __name__ == "__main__":
    agents.cli.run_app(server)
