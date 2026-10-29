import Foundation

/// The model decides what kind of turn this is; the host checks the decision and runs the matching path.
/// One short, grammar-constrained call (Apple Intelligence when available, otherwise the local model under a JSON
/// schema). The pattern rules in ResearchPolicy remain the fallback when no classifier answers in time, and a few
/// sanity checks override the model where the evidence is unambiguous (attached files mean research).
enum TurnIntent: String {
    case chat               // conversation: answer from personality and knowledge, no tools, no task
    case authorDocument     // write a document from Squipi's own knowledge and file it
    case localFiles         // look at, list or open files and folders on this Mac
    case research           // data, documents or sources: the full loop with tools and checks

    struct Decision { let intent: TurnIntent; let filename: String?; let backend: String; let elapsed: Double }

    static let schema: [String: Any] = ["type": "object", "properties": [
        "intent": ["type": "string", "enum": ["chat", "author_document", "local_files", "research"]],
        "filename": ["type": ["string", "null"], "description": "the output file name the user asked for, if any"],
        "reason": ["type": "string", "maxLength": 160]],
        "required": ["intent", "reason"], "additionalProperties": false]

    static let guidance = """
    Classify the user's latest message for a desktop research companion. Reply with one JSON object only.
    intent values:
    - chat: greetings, feelings, small talk, questions answered from general knowledge (including explaining a method, concept or term when no file or data is involved) or about the assistant itself, no file produced.
    - author_document: the user wants a document, note, memo, letter or introduction written from the assistant's own knowledge (not from data or an attached source) and saved as a file.
    - local_files: list, find, open or organise files or folders on this Mac (Downloads, Desktop, a granted folder); no analysis.
    - research: anything that needs data, attached or named documents, calculations, charts, reports from sources, online evidence, memory or reminders.
    If files are attached or a dataset/report is named, the intent is research. filename: the exact name the user gave for an output file, else null.
    """

    /// A continuation turn wraps the user's request in host instructions ("Continue the current user-requested
    /// research task… Original user request:\n…"); the classifier must see the user's words, not the wrapper.
    static func userWords(_ text: String) -> String {
        guard text.hasPrefix("Continue the current user-requested research task."), let range = text.range(of: "Original user request:\n") else { return text }
        var rest = String(text[range.upperBound...])
        for marker in ["\n\nResearch objective id:", "\nResearch objective id:"] { if let r = rest.range(of: marker) { rest = String(rest[..<r.lowerBound]) } }
        return rest.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    @MainActor static func classify(text: String, hasAttachments: Bool, provider: any ChatProvider, key: String) async -> Decision? {
        if hasAttachments { return Decision(intent: .research, filename: nil, backend: "attachments", elapsed: 0) }
        let started = Date()
        let prompt = "Latest message:\n\(String(userWords(text).prefix(1_200)))"
        var raw: String? = nil; var backend = ""
        if AppleIntelligence.available {
            raw = try? await AppleIntelligence.respond(instructions: guidance + "\nJSON keys: intent, filename, reason.", prompt: prompt); backend = "apple-intelligence"
        }
        if raw == nil || parse(raw!) == nil {
            let messages = [ChatMessage(role: "system", content: guidance), ChatMessage(role: "user", content: prompt)]
            if let ollama = provider as? OllamaProvider { raw = try? await ollama.completeJSON(messages: messages, schema: schema, maxTokens: 160).text; backend = "ollama-json-schema" }
            else if let mlx = provider as? MLXProvider { raw = try? await mlx.completeJSON(messages: messages, schema: schema, maxTokens: 160).text; backend = "mlx-json-mode" }
        }
        guard let raw, let parsed = parse(raw) else { return nil }
        NSLog("INTENT %@ (%@, %.1fs): %@", parsed.0.rawValue, backend, Date().timeIntervalSince(started), parsed.2.prefix(100) as CVarArg)
        return Decision(intent: parsed.0, filename: parsed.1, backend: backend, elapsed: Date().timeIntervalSince(started))
    }

    static func parse(_ text: String) -> (TurnIntent, String?, String)? {
        guard let a = text.firstIndex(of: "{"), let b = text.lastIndex(of: "}"), a < b,
              let data = String(text[a...b]).data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let value = object["intent"] as? String else { return nil }
        let intent: TurnIntent
        switch value { case "chat": intent = .chat; case "author_document": intent = .authorDocument; case "local_files": intent = .localFiles; case "research": intent = .research; default: return nil }
        let filename = (object["filename"] as? String)?.trimmingCharacters(in: .whitespaces)
        return (intent, (filename?.isEmpty ?? true) ? nil : filename, object["reason"] as? String ?? "")
    }
}
