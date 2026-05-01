# 🤖 TradBot: Multi-Horizon Autonomous Trading Intelligence

TradBot is a sophisticated, autonomous trading system powered by a multi-agent AI architecture. It leverages Retrieval-Augmented Generation (RAG) to ground trading decisions in real-time market data, historical performance, and custom knowledge bases.

![Dashboard Preview](https://via.placeholder.com/1200x600.png?text=TradBot+Digital+Command+Center+Dashboard) <!-- Use generate_image later if needed -->

## 🚀 Key Features

- **Multi-Horizon AI Intelligence:** Four specialized agents working in tandem:
    - **Long-Term Agent:** Strategic trend analysis and portfolio rebalancing.
    - **Medium-Term Agent:** Swing trading and momentum identification.
    - **Hustle Agent:** High-frequency opportunity scanning and scalp signals.
    - **Short-Term Agent:** Immediate execution and local price action monitoring.
- **RAG-Powered Knowledge Layer:** Uses a SQLite-based vector store to ingest market rules, technical indicators, and historical context for grounded AI decision-making.
- **Digital Command Center:** A sleek, high-tech dashboard built with Alpine.js and Tailwind CSS, providing real-time visibility into agent states, portfolio health, and live trades.
- **Safety First:** Integrated `bot_running` guards, manual AI locks, and a robust backtesting engine to validate strategies before going live.
- **Multi-Provider Support:** Seamlessly switches between OpenAI, Anthropic, OpenRouter, and local models (Ollama).

## 🛠️ Tech Stack

- **Backend:** FastAPI (Python 3.12+)
- **Frontend:** Alpine.js, Tailwind CSS (Vanilla JS dashboard)
- **Database/Vector Store:** SQLite with Vector search capabilities
- **Exchange Integration:** Binance API
- **AI Integration:** Structured LLM calls with unified provider handling

## 📋 Quick Start

### Prerequisites
- Python 3.12+
- Binance API Keys
- AI Provider API Keys (OpenAI, Anthropic, etc.)

### Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/omar-hesham/tradbot.git
   cd tradbot
   ```
2. Set up the virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r trading_bot/requirements.txt
   ```
4. Configure environment variables:
   ```bash
   cp trading_bot/.env.example trading_bot/.env
   # Edit trading_bot/.env with your API keys
   ```

### Running the App
Start the backend and dashboard:
```bash
python trading_bot/main.py
```
The dashboard will be available at `http://localhost:8005`.

## 🧠 Architecture Overview

The system operates on a "Sense-Think-Act" loop:
1. **Sense:** Ingests live ticker data and news via the RAG system.
2. **Think:** The 4-agent collective analyzes the data across different timeframes.
3. **Act:** The Risk Manager validates the signals and executes trades via the Binance API.

## ⚖️ License
MIT License - See [LICENSE](LICENSE) for details.

---
*Built with ❤️ by Omar Hesham Safwat*
