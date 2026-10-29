import Foundation
import FoundationModels

/// On-device Apple Intelligence helpers. Used only for small, well-posed jobs (contract extraction,
/// span notes). Never a dependency for correctness: every result is validated by deterministic code,
/// and every caller has an Ollama/MLX/Nebius fallback.
@MainActor enum AppleIntelligence {
    static var enabled: Bool { UserDefaults.standard.object(forKey: "appleIntelligenceAssist") as? Bool ?? true }
    static var available: Bool {
        guard enabled else { return false }
        if case .available = SystemLanguageModel.default.availability { return true }
        return false
    }
    static func respond(instructions: String, prompt: String) async throws -> String {
        let lease = try InferenceCoordinator.shared.beginModel()
        defer { InferenceCoordinator.shared.endModel(lease) }
        return try await LanguageModelSession(instructions: instructions).respond(to: prompt).content
    }
}

/// Turns the user's sentence into host-checkable requirements before any tool runs. Quotes must be
/// substrings of the submitted instruction; kinds must be ones the worker can verify. The worker
/// re-validates everything in `set_research_requirements`, so a model cannot invent a contract.
@MainActor enum RequirementContract {
    static let kinds: Set<String> = ["artifact", "analysis", "chart", "columns_absent", "columns_present", "pii_absent", "source_used"]

    /// Columns the user asked to keep despite an anonymisation request. Phrases: "keep …", "retain …", "leave …",
    /// "preserve …", "except (for) …", "but keep …". Words are matched against the selected data's column names
    /// (a word inside the name, e.g. "gps" → village_gps; "coordinates" → gps/lat/lon columns).
    /// True when the request itself asks for personal data to be removed, hidden or pseudonymised.
    static func asksForPIIRemoval(_ instruction: String) -> Bool {
        instruction.range(of: #"\b(?:pii|personal(?:ly)?[ -]?(?:identif\w*|data|information|details)|identif(?:y|ying|iable|iers?)\b|anonymi[sz]\w*|pseudonymi[sz]\w*|de-?identif\w*|redact\w*|sensitive|confidential|privacy|private data|remove (?:the )?(?:names?|phones?|addresses|emails?)|strip (?:the )?(?:names?|phones?)|no names|without (?:the )?names)\b"#, options: [.regularExpression, .caseInsensitive]) != nil
    }

    /// True when the request names the column or one of the words in its name ("income" for monthly_income).
    static func requestEchoes(_ column: String, in instruction: String) -> Bool {
        if instruction.localizedCaseInsensitiveContains(column) { return true }
        let words = Set(instruction.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init))
        return column.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber }).map(String.init)
            .contains { $0.count >= 3 && (words.contains($0) || words.contains($0 + "s") || ($0.hasSuffix("s") && words.contains(String($0.dropLast())))) }
    }

    static func keptColumns(instruction: String, knownColumns: Set<String>) -> [String] {
        let text = instruction.lowercased()
        let pattern = #"\b(?:keep|retain|leave|preserve|except(?: for)?|but keep|apart from)\s+(?:the\s+|all\s+|only\s+)?([a-z0-9_][a-z0-9_ ,/-]{1,80}?)(?=\s+(?:column|columns|field|fields|variable|variables|as is|intact|in|out of|for|so|and give|and save|and export)\b|[.;,]|$)"#
        guard let rx = try? NSRegularExpression(pattern: pattern) else { return [] }
        var words: [String] = []
        for m in rx.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let r = Range(m.range(at: 1), in: text) else { continue }
            words += text[r].components(separatedBy: CharacterSet(charactersIn: " ,/")).filter { $0.count >= 3 }
        }
        guard !words.isEmpty else { return [] }
        let synonyms: [String: [String]] = ["coordinates": ["gps", "lat", "lon", "lng", "latitude", "longitude", "geo"], "location": ["gps", "lat", "lon", "geo"], "gps": ["gps"], "phone": ["phone", "mobile", "tel"], "names": ["name"], "name": ["name"]]
        var kept: [String] = []
        for column in knownColumns.sorted() {
            let lower = column.lowercased()
            for word in words {
                let needles = synonyms[word] ?? [word]
                if needles.contains(where: { lower.contains($0) }), !kept.contains(column) { kept.append(column) }
            }
        }
        return kept
    }
    struct Result { let items: [[String: Any]]; let backend: String; let elapsed: Double; var summary: String }

    static let guidance = """
    Extract checkable requirements from a research request. Rules: every quote is copied verbatim from the request (no paraphrase); only include what the user actually asked; kinds: artifact (a file: give format, exact filename if named), analysis (statistics or measures: list the measure columns and grouping columns named), chart (graphs requested: list the subject columns), columns_absent (columns the user wants removed or excluded), columns_present (columns the user wants kept), pii_absent (the user asked to remove personal information / PII / identifying data), source_used (a named input file). Do not invent units, filenames, study designs or causal claims. At most 12 items. If the request is only a question with no deliverable, return an empty list.
    """

    static func schema() -> [String: Any] {
        let item: [String: Any] = ["type": "object", "properties": [
            "kind": ["type": "string", "enum": Array(kinds).sorted()],
            "quote": ["type": "string"], "description": ["type": "string"],
            "format": ["type": "string"], "filename": ["type": "string"],
            "columns": ["type": "array", "items": ["type": "string"]],
            "groups": ["type": "array", "items": ["type": "string"]]],
            "required": ["kind", "quote", "description"], "additionalProperties": false]
        return ["type": "object", "properties": ["requirements": ["type": "array", "maxItems": 12, "items": item]], "required": ["requirements"], "additionalProperties": false]
    }

    /// Validate and normalise model output into worker requirement rows. Invalid rows are dropped, never repaired.
    static func normalise(_ raw: [[String: Any]], instruction: String, history: [String], sourceIDs: [String], knownColumns: Set<String>) -> [[String: Any]] {
        let corpus = ([instruction] + history).map { $0.lowercased() }
        var out: [[String: Any]] = []
        var used = Set<String>()
        for (index, row) in raw.prefix(12).enumerated() {
            guard let kind = (row["kind"] as? String)?.lowercased(), kinds.contains(kind),
                  var quote = row["quote"] as? String else { continue }
            quote = quote.trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: "\"'“”‘’.")))
            guard quote.count >= 2, quote.count <= 1000, corpus.contains(where: { $0.contains(quote.lowercased()) }) else { continue }
            var item: [String: Any] = ["id": "contract-\(kind)-\(index + 1)", "kind": kind, "quote": quote,
                                       "description": String((row["description"] as? String ?? quote).prefix(600))]
            if kind == "artifact" {
                // A reading or a question produces no file; only a request with an output verb can carry an artifact row.
                guard instruction.range(of: #"\b(?:create|make|generate|produce|return|deliver|export|save|write|give me|give us|put it in|prepare|build|turn (?:this|it) into)\b"#, options: [.regularExpression, .caseInsensitive]) != nil else { continue }
                let format = (row["format"] as? String ?? "").lowercased().trimmingCharacters(in: .whitespaces)
                let filename = (row["filename"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: "\"'“”‘’")))
                let inferred = ["xlsx", "csv", "docx", "pptx", "pdf", "html"].first { filename.lowercased().hasSuffix("." + $0) }
                guard let fmt = inferred ?? (["xlsx", "csv", "docx", "pptx", "pdf", "html"].contains(format) ? format : nil) else { continue }
                item["format"] = fmt
                if !filename.isEmpty, filename == URL(fileURLWithPath: filename).lastPathComponent, instruction.localizedCaseInsensitiveContains(filename) { item["filename"] = filename }
                if used.insert("artifact:\(fmt)").inserted == false { continue }
            }
            let columns = (row["columns"] as? [String] ?? []).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            let groups = (row["groups"] as? [String] ?? []).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            if ["analysis", "chart", "columns_absent", "columns_present", "pii_absent"].contains(kind), knownColumns.isEmpty { continue } // no dataset selected: nothing to check
            if ["analysis", "chart", "columns_absent", "columns_present"].contains(kind) {
                // Keep only column names that exist in the selected data or appear in the request text. For an analysis or
                // chart the request must also echo the column (a word of its name): "analyze this dataset and produce key
                // insights" bound 13 schema columns the user never named, and no whole-dataset tool could meet it (27 Oct).
                let keep = columns.filter { column in
                    guard knownColumns.contains(column) || instruction.localizedCaseInsensitiveContains(column) else { return false }
                    return !["analysis", "chart"].contains(kind) || Self.requestEchoes(column, in: instruction)
                }
                // A data requirement without any real column is a model invention, not a check.
                guard !keep.isEmpty || !groups.filter({ knownColumns.contains($0) }).isEmpty else { continue }
                if kind == "columns_absent" || kind == "columns_present" { guard !keep.isEmpty else { continue } }
                if !keep.isEmpty { item["columns"] = Array(keep.prefix(50)) }
                let keepGroups = groups.filter { knownColumns.contains($0) || instruction.localizedCaseInsensitiveContains($0) }
                if !keepGroups.isEmpty { item["groups"] = Array(keepGroups.prefix(3)) }
            }
            if kind == "pii_absent" {
                // Only a request that asks for it: "analyze entire dataset and important variables" on a file named
                // …_PII_Test produced a "remove personal information" requirement the user never made, and the turn
                // went into repair after a correct analysis (27 Oct 21:15). The words are checked here, not by the model.
                guard Self.asksForPIIRemoval(instruction) else { continue }
                // The user decides what counts as identifying: "keep the village GPS", "except the coordinates" become
                // exceptions the deterministic PII review skips (host-derived from the request, never model-invented).
                let kept = Self.keptColumns(instruction: instruction, knownColumns: knownColumns)
                if !kept.isEmpty { item["columns"] = kept }
            }
            if ["pii_absent", "columns_absent", "columns_present", "source_used", "analysis", "chart"].contains(kind), !sourceIDs.isEmpty { item["source_ids"] = sourceIDs }
            if kind == "source_used" { continue } // the host already records source identity; a model copy adds nothing
            out.append(item)
        }
        return out
    }

    static func establish(instruction: String, history: [String], sourceIDs: [String], knownColumns: Set<String>,
                          provider: any ChatProvider, key: String, objectiveID: String) async -> Result? {
        let started = Date()
        let prompt = "User request:\n\(instruction)\n\nKnown column names of the selected data: \(Array(knownColumns).sorted().prefix(60).joined(separator: ", "))"
        var raw: [[String: Any]]? = nil
        var backend = ""
        if AppleIntelligence.available {
            // Plain-text JSON from the on-device model (the guided-generation macros need Xcode's
            // toolchain); every row is validated by `normalise` and again by the worker, so a
            // malformed reply costs one fallback call, never a wrong contract.
            do {
                let text = try await AppleIntelligence.respond(instructions: guidance + "\nReply with only a JSON object of the form {\"requirements\":[{\"kind\":\"…\",\"quote\":\"…\",\"description\":\"…\",\"format\":\"\",\"filename\":\"\",\"columns\":[],\"groups\":[]}]} and nothing else.", prompt: prompt)
                if let a = text.firstIndex(of: "{"), let b = text.lastIndex(of: "}"), a < b,
                   let data = String(text[a...b]).data(using: .utf8),
                   let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let rows = object["requirements"] as? [[String: Any]] {
                    raw = rows; backend = "apple-intelligence"
                }
            } catch { NSLog("CONTRACT apple backend failed: %@", error.localizedDescription) }
        }
        if raw == nil {
            do {
                let messages = [ChatMessage(role: "system", content: guidance + "\nReturn only a JSON object: {\"requirements\":[{kind, quote, description, format, filename, columns, groups}]}"),
                                ChatMessage(role: "user", content: prompt)]
                let reply: ModelReply
                if let ollama = provider as? OllamaProvider {
                    reply = try await ollama.completeJSON(messages: messages, schema: schema(), maxTokens: 900); backend = "ollama-json-schema"
                } else if let mlx = provider as? MLXProvider {
                    reply = try await mlx.completeJSON(messages: messages, schema: schema(), maxTokens: 900); backend = "mlx-json-mode"
                } else {
                    reply = try await provider.complete(messages: messages, tools: nil, key: key, stream: false, temperature: 0, maxTokens: 900, think: false, onDelta: nil); backend = provider.isLocal ? "mlx-json" : "cloud-json"
                }
                let text = reply.text
                if let a = text.firstIndex(of: "{"), let b = text.lastIndex(of: "}"), a < b,
                   let data = String(text[a...b]).data(using: .utf8),
                   let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    raw = object["requirements"] as? [[String: Any]] ?? []
                }
            } catch { NSLog("CONTRACT model backend failed: %@", error.localizedDescription) }
        }
        guard let raw else { return nil }
        let items = normalise(raw, instruction: instruction, history: history, sourceIDs: sourceIDs, knownColumns: knownColumns)
        let elapsed = Date().timeIntervalSince(started)
        guard !items.isEmpty else { return Result(items: [], backend: backend, elapsed: elapsed, summary: "Request read (\(backend)): no file name, format or measure to verify, so this runs as an ordinary analysis turn.") }
        do {
            _ = try await Worker.shared.call("set_research_requirements", ["requirements": items], executionScope: objectiveID)
        } catch {
            NSLog("CONTRACT worker rejected requirements: %@", error.localizedDescription)
            return Result(items: [], backend: backend, elapsed: elapsed, summary: "Requirement contract rejected by the host checks: \(error.localizedDescription)")
        }
        let summary = "Checkable requirements recorded (\(backend), \(String(format: "%.1f", elapsed)) s): " + items.map { item in
            var line = item["kind"] as? String ?? ""
            if let f = item["filename"] as? String { line += " \(f)" } else if let f = item["format"] as? String { line += " .\(f)" }
            if let c = item["columns"] as? [String], !c.isEmpty { line += " [\(c.joined(separator: ", "))]" }
            if let g = item["groups"] as? [String], !g.isEmpty { line += " by \(g.joined(separator: ", "))" }
            return line
        }.joined(separator: "; ")
        return Result(items: items, backend: backend, elapsed: elapsed, summary: summary)
    }
}
