# 🎙️ Todemy: Voice-to-Voice AI Agent Ecosystem

This repository contains the backend and frontend for the Todemy Voice ecosystem—an ultra-low latency, multi-agent system designed for Indian parents and children. It leverages a high-performance hybrid AI stack to provide seamless, conversational support.

## 🚀 The Hybrid Architecture

To achieve the best balance of speed, contextual accuracy, and natural sound, this project uses a "Best-of-Breed" stack:

* **Ears (STT):** OpenAI `gpt-4o-mini-transcribe` (Locked to English, optimized for Indian accents).
* **Brain (LLM):** OpenAI `gpt-4o-mini` (High-speed reasoning and reliable tool-routing).
* **Mouth (TTS):** Google Cloud Gemini Voices (Soothing `en-IN` male persona for natural regional pronunciation).
* **Orchestration:** LiveKit Agents SDK & Silero VAD (Voice Activity Detection).

---

## 🤖 Featured Agents

### 1. The Parent Expert Swarm (Multi-Mode)
A sophisticated routing agent that identifies user intent and "silently" hands off the conversation to specialized experts:
* **Meal Agent:** Personalized, toddler-friendly nutrition and meal planning.
* **Parenting Agent:** Emotional support, routine building, and tantrum management.
* **Learning Agent:** Educational activities and developmental milestones.
* **Health & Wellness:** Sleep hygiene, physical wellness, and habit tracking.

### 2. The Nanny Avatar (Single-Mode)
A gentle, playful persona designed specifically for 3-year-olds. It uses simplified language, storytelling, and counting games to engage children in a safe environment.

---

## 🛠️ Key Features
* **Ultra-Low Latency:** Token-to-audio streaming with a Time-to-First-Byte (TTFB) of under 1 second.
* **Safety Guardrails:** Hard-coded constraints to prevent system crashes and ensure age-appropriate content.
* **Silent Transfers:** Specialized logic that moves the user between expert agents without breaking the conversational flow.
* **Indian English Optimization:** Regional STT locking and TTS localization for words like *Roti*, *Paneer*, and *Khichdi*.

---

## 💻 Tech Stack
* **Backend:** Python (FastAPI/LiveKit Agents)
* **Real-time:** WebRTC via LiveKit Cloud
* **Models:** OpenAI GPT-4o family & Google Cloud Text-to-Speech
* **Infrastructure:** Render (Deployment), GitHub (CI/CD)

---

## 🚀 Getting Started

### 1. Environment Setup
Create a `.env` file with the following:
```env
LIVEKIT_URL=<your-url>
LIVEKIT_API_KEY=<your-key>
LIVEKIT_API_SECRET=<your-secret>
OPENAI_API_KEY=<your-openai-key>
GOOGLE_APPLICATION_CREDENTIALS=google_credential.json
GOOGLE_TTS_VOICE=en-IN-Wavenet-B
```

### 2. Installation
```bash
pip install -r requirements.txt
```

### 3. Run Locally (Console Mode)
```bash
python voice_agent.py console
```

---

## 📦 Repository Structure
* `voice_agent.py`: The core logic for the multi-agent Swarm.
* `index.html`: The web frontend visualizer.
* `ui_server.py`: Server to host the frontend interface.
* `start.sh`: Shell script for production deployment.
