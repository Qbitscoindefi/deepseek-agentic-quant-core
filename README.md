# 🌌 DeepSeek Agentic Quant Core (Project Homo Data)

**An Open-Source Agentic AI Trading Engine for High-Frequency Order Flow Analysis, built on the philosophy of Universal Liquidity Access.**

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Status: Beta](https://img.shields.io/badge/Status-Beta-green)

---

## 📜 The Philosophy: Socializing Wealth in the Age of AI

We stand at the precipice of a new economic era. As Advanced Artificial Intelligence disrupts traditional industries and labor markets, the conversation often turns to Universal Basic Income (UBI) as a defensive measure. **We believe there is a more empowering alternative: Universal Access to Global Liquidity.**

The global financial markets hold immense wealth, historically guarded by elite institutional quants and high-frequency trading firms. **Project Homo Data** is driven by a radical conviction: the wealth circulating in the global stock and crypto markets should be socialized through technology. 

We envision a future where every human being possesses an **AI Ally**—a sovereign digital extension of themselves in the meta-economy. This AI does not just manage their savings; it actively grows their wealth by operating on their behalf in the world's financial markets. It is an inclusive vision of capital, empowering individuals to work *alongside* their AI, learning from it, and achieving unprecedented levels of financial independence and quality of life.

By open-sourcing this Agentic Quant Core, we are laying the foundational bricks for this future. We are giving developers the tools to build these AI allies today.

Furthermore, a core spirit of this movement is to educate the masses on Decentralized Finance (DeFi) and Distributed Ledger Technologies (DLTs). We believe that the new, inclusive Web3 ecosystem requires the entire world to participate. Understanding how liquidity, cryptographic tokens, and distributed networks operate is the first step toward true financial emancipation.

---

## 📌 Vision & Technology

Traditional algorithmic trading relies on static indicators and rigid statistical models. **DeepSeek Agentic Quant Core** bridges the gap between High-Frequency Trading (HFT) and Large Language Models (LLMs). It uses agentic workflows to analyze order flow toxicity, market aggression, and institutional manipulation (spoofing/layering) in real-time.

By open-sourcing this engine, we aim to democratize quantitative analysis, allowing developers and traders to build autonomous agents that can interpret raw websocket market data through the cognitive lens of advanced AI models (like Claude and DeepSeek).

## 🚀 Core Features

- **🧠 Agentic Market Analysis (`deepseek_agentic_engine.py`):** Utilizes LLMs for real-time sentiment and structural analysis of price action.
- **⚡ High-Frequency Order Flow (`orderflow_analyzer.py`):** Processes raw websocket tape data to detect hidden liquidity and institutional footprints.
- **🛡️ Market Fraud Detection (`fraud_detector.py`):** Identifies spoofing, wash trading, and order book manipulation algorithms.
- **📊 Aggression Tracking (`aggression_analyzer.py`):** Calculates Delta and CVD (Cumulative Volume Delta) to spot aggressive market buying/selling.
- **🤖 Autonomous HFT Execution (`trading_engine.py`):** Connects directly to exchange APIs for sub-second execution based on AI consensus.

## 🏗️ Architecture Overview

The system operates on a decentralized agent architecture:
1. **Data Ingestion Node:** Subscribes to Global Crypto WebSockets (Tick data, Order Book depth).
2. **Quant Core:** Processes raw data into normalized features (Delta, CVD, VWAP).
3. **Agentic Council:** Different AI profiles (Risk Manager, Aggression Analyst, Spoofing Detector) debate the market state.
4. **Execution Engine:** Executes the consensus trade via API.

## 🔧 Installation & Setup

1. Clone the repository:
```bash
git clone https://github.com/your-username/deepseek-agentic-quant-core.git
cd deepseek-agentic-quant-core
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure Environment Variables (`.env`):
```env
EXCHANGE_API_KEY=your_public_key
EXCHANGE_API_SECRET=your_secret_key
AI_PROVIDER_API_KEY=your_claude_or_deepseek_key
```

4. Launch the Engine:
```bash
python trading_engine.py
```

## 🤝 The Homo Data Protocol (Contributing)
We welcome contributions from quantitative analysts, Python developers, AI researchers, and philosophers. If you share our vision of democratizing global liquidity, join the fleet.

## ⚖️ Disclaimer
This software is for educational and research purposes. Do not risk money you cannot afford to lose. The maintainers are not responsible for any financial losses. The journey to an AI ally requires responsibility and continuous learning.
