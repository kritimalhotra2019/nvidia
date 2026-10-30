# Third-party attribution

Squipi builds on components of **Ribbit / Frog** by Dmitry Pyanov (https://github.com/Pyanov/frog), MIT License, upstream commit `796523d05aef49fcaca794fdc7b3f6f04c7358ee`. The upstream copyright notice is preserved in `LICENSE`. Modified components include the Swift/AppKit application shell, pet panel, hotkeys, Apple speech, memory and settings, as well as web scaffolding and the character interface.

Squipi adds its own flapjack octopus character, personality, research-agent harness, Python research worker, research tools, providers and other features (© 2026 Kriti Malhotra; MIT).

Third-party software includes LLM.swift and llama.cpp (MIT), three.js (MIT), pandas, NumPy, SciPy, PyArrow, openpyxl, pypdf, python-docx and python-pptx. Each dependency has its own licence.

Local research options include Qwen 3.5 through separately installed Ollama or experimental Apple MLX / MLX-LM. NVIDIA Nemotron and Kimi are available through Nebius Token Factory. Optional NVIDIA Kumo Forecast, Relational and Tabular specialists require separate setup and weights. Model and service terms apply separately from Squipi's MIT licence. The built-in conversation component also supports separately obtained Gemma or Qwen models, subject to their respective terms.

Model weights are not included in this repository. A synthetic household test dataset is provided in examples.
