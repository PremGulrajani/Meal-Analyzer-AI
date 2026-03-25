Meal Analyzer AI - An an AI Personalized Nutritional Coach

Meal Analyzer AI is a cloud-native nutrition assistant capable of delivering structured, reliable nutritional data, offering contextual dietary guidance, and helping me strategically plan my meals throughout the day to meet my fitness goals.

**// Architecture //**

User (Client) Layer: Facilitates the mult-modal interaction through browser-based interface. 
Text & Voice Input: Users can enter meal descriptions via text or record a 4-second audio clip.
Speech-to-Text Integration: Audio clips are sent to the Cloud Speech-to-Text API, which transcribes the recording into sanitized text before analysis processing. 
Application Layer (Cloud Run: FastAPI Backend Service + Web UI): Acts as the central orchestrator, manages the data flow between the user and external services
Monolithic Serving: To reduce deployment complexity, the FastAPI backend directly serves the HTML/JavaScript Web UI via the /ui route. 
Containerized FastAPI Backend: Services and exposes API endpoints (e.g., /chat, /transcribe, /set_goals, /reset_meals).
Security and Data Governance: The backend implements security controls, including input sanitization and secure API management.
Managed Services / External APIs: The intelligence and persistence of the agent that utilizes external tools to accomplish the task.
Vertex AI (Gemini Agent): The “brain” of the system uses the gemini-2.0-flash-lite model. It operates in two stages:
Decides if external tools (USDA API) are needed
Reasoning, response generation, and agentic behavior.
Cloud Firestore: Serves a dual purpose as both the State Store and Food Cache. It persists user-specific daily macronutrient and caloric goals, and caches previous USDA lookups using SHA1 hashes to minimize external API latency and costs.  
USDA FoodData REST API: Provides the ground-truth nutritional data for concrete food items identified by the Gemini Agent.

**// Cloud Services Used //**
Google Cloud Run (Compute)
Role: Serves as the primary hosting environment for the Python FastAPI backend and Web UI in a container.
Reasoning: Cloud Run provides a fully managed serverless environment that  provides auto-scaling, simple deployment, and minimal operational overhead.
Vertex AI Gemini 2.0 Flash Lite (AI Reasoning)
Role: The core intelligence engine of the application
Reasoning: The Gemini 2.0 Flash Lite model was selected for its balance of high speed inferences, and sophisticated agentic capabilities. It is specifically used for intent classification, deciding on whether to look up a food item, or provide a general estimate, and nutritional synthesis.
Cloud Firestore (Data Persistence & Caching)
Role: Acts as the systems primary NoSQL document database
Reasoning: Firestore is critical for two data flows:
User State: Persist daily nutritional goals and current consumption totals for each user.
Food Cache: Stores SHA1-hashed results of previous nutritional lookups to reduce external API latency and reduce costs through repeated external lookups.
Cloud Speech-to-Text (Voice Processing)
Role: Converts recorded audio clips from the frontend into text to be used for processing through the data pipeline.
Reasoning: The API’s ability to handle noisy audio and accurately transcribe foods from short audio clips provides a seamless user experience
USDA FoodData Central API (External Data Tool)
Role: An external REST API used as a reliable, factual “grounding” tool
Reasoning: To avoid potential AI-related "hallucinations" regarding specific calorie counts, the system queries the USDA’s verified database for concrete food items. (e.g. “4 oz steak”) identified by the AI agent. 
Cloud Secret Manager (Security and Governance)
Role: Securely stores the USDA_API_KEY and other sensitive environment variables
Reasoning: Instead of hardcoding the keys in config.py, the application retrieves them at runtime. These variables are injected directly into the container using Secret Manager. This was critical to prevent API key exposure. 
Google Cloud IAM (Security & Governance)
Role: Manages the Service Account permissions for the Cloud Run instance
Reasoning: By using the principle of least privilege, I made sure that the assistant only has the permissions that give it enough access to perform the essential functions of the application.. 
