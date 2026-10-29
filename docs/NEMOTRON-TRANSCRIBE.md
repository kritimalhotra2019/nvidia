# Meeting audio API deferred

The user deferred Nemotron Transcribe API integration on 29 October 2026. The current app uses **Apple Speech for local transcription and Apple Intelligence for meeting notes**. There is no audio API selector or endpoint/key setup in Me, and `Meeting.swift` never reads the previous API preference or calls the API client. A previously saved `meetingTranscriptionEngine=nemotron` preference has no effect.

The unused `NemotronTranscribeAPI.swift` and `NemotronTranscribeProtocol.swift` prototype sources and their offline protocol fixtures remain for possible future work. They are not reachable from meeting processing or settings and must not be described as an active feature or a successful live demonstration. No Transcribe weights were downloaded and no real audio was uploaded during implementation.

Retained improvements:

- Save the transcript before writing meeting notes.
- Preserve recordings and saved transcripts when processing is cancelled or note writing fails.
- Show transcription provenance and honest microphone/call channel labels; individual-speaker diarisation is not claimed.
- Keep personal vocabulary in the local Apple speech path.
- Respect the shared model lease while Apple Speech or Apple Intelligence is running.

The focused meeting fixture checks use synthetic audio, a fixture transcriber and fixture summaries. They verify transcript persistence, failure handling, provenance and stale cloud preferences without using a microphone, model, network connection or user data.

Future API work would require a verified compatible endpoint and credentials for that host. An NVIDIA API key alone was not sufficient to establish a supported endpoint for this model. Vendor references retained for future review:

- [NVIDIA launch article](https://huggingface.co/blog/nvidia/nemotron-transcribe-granary)
- [Official model and vLLM protocol](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B#inference-setup-and-prompts)
- [DeepInfra API](https://deepinfra.com/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B/api)
