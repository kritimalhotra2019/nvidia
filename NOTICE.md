# Attribution

Squipi is a fork of **Ribbit / Frog** by Dmitry Pyanov — https://github.com/Pyanov/frog — MIT License,
upstream commit `796523d05aef49fcaca794fdc7b3f6f04c7358ee` (2026-10-08). The upstream copyright notice is
preserved in `LICENSE`. Inherited from upstream, in modified form: the Swift/AppKit application shell
(`Sources/Squipi/*`: pet panel, hotkeys, Apple speech, LLM.swift brain, memory, meeting notes,
settings hub), the web scaffolding (`web/index.html`, `web/vite.config.js`, `web/tools/capture.js`) and
the character contract (`pet.setState / setLevel / lookAt / setVelocity / setTalking`).

Original to Squipi (MIT, © 2026 Kriti Malhotra): the flapjack octopus in `web/src/main.js`, the persona,
`worker/` (the Python analysis worker), and `Worker.swift`, `Workspace.swift`, `Analysis.swift`,
`Providers.swift`, `Chat.swift`.

Third-party components, fetched at build or first run, each under its own licence:
LLM.swift (MIT) with llama.cpp (MIT);
three.js (MIT); Gemma 4 GGUFs by Unsloth (Gemma Terms of Use) or Qwen2.5 (Apache-2.0) as the built-in brain;
Ollama (MIT, installed separately by the user) serving Qwen3.5 (Apache-2.0) or NVIDIA Nemotron (NVIDIA Open Model
Licence) as the tool-using brain; pandas, numpy, scipy, pyarrow (BSD/Apache), openpyxl (MIT), pypdf (BSD),
python-docx (MIT), python-pptx (MIT). No model weights or datasets are included in this repository.
