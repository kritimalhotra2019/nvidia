import SwiftUI

/// One saved preference drives both native surfaces and the WebKit character.
enum SquipiColour: String, CaseIterable, Identifiable {
    case blue, lavender, lime
    var id: String { rawValue }
    var label: String { switch self { case .blue: return "Ocean blue"; case .lavender: return "Lavender"; case .lime: return "NVIDIA-inspired lime" } }
    // Dark actions retain readable white labels; the character uses the brighter companion colour.
    var accent: UInt { switch self { case .blue: return 0x286B98; case .lavender: return 0x7150A0; case .lime: return 0x477000 } }
    var soft: UInt { switch self { case .blue: return 0xE1F0F8; case .lavender: return 0xEEE6F6; case .lime: return 0xEAF4CF } }
    var background: UInt { switch self { case .blue: return 0xF4F7FA; case .lavender: return 0xF8F5FA; case .lime: return 0xF6F8EF } }
    static var current: Self { Self(rawValue: UserDefaults.standard.string(forKey: "squipiColour") ?? "") ?? .blue }
}

extension Color {
    init(squipiHex: UInt) { self.init(red: Double((squipiHex >> 16) & 255)/255, green: Double((squipiHex >> 8) & 255)/255, blue: Double(squipiHex & 255)/255) }
}


struct PersonalizationView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    @AppStorage("squipiColour") private var colour = "blue"
    @AppStorage("squipiLanguage") private var language = "en"
    @AppStorage("squipiOutfit") private var outfit = "classic"
    @AppStorage("squipiAccessory") private var accessory = "none"
    @AppStorage("squipiHat") private var hat = "none"
    @AppStorage("squipiGlasses") private var glasses = true
    @State private var speechSupport = ""
    private var app: AppDelegate? { NSApp?.delegate as? AppDelegate }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionTitle(text: SquipiLanguage.text("Appearance"))
            Picker("Colour", selection: $colour) {
                ForEach(SquipiColour.allCases) { item in Text(item.label).tag(item.rawValue) }
            }
            Picker("Clothes", selection: $outfit) {
                Text("Classic").tag("classic"); Text("Shirt").tag("shirt")
                Text("Pants").tag("pants"); Text("Shorts").tag("shorts"); Text("Dress").tag("dress")
                Text("Kurta").tag("kurta"); Text("Indian Ajrak — Kurta").tag("ajrakKurta"); Text("Indian Ajrak — Dress").tag("ajrakDress")
                Text("Research coat").tag("coat"); Text("Jensen’s leather jacket").tag("jensenJacket")
            }
            Picker("Accessory", selection: $accessory) {
                Text("None").tag("none"); Text("Scarf").tag("scarf"); Text("Bow tie").tag("bowtie")
            }
            Picker("Headwear", selection: $hat) {
                Text("None").tag("none"); Text("Beret").tag("beret"); Text("Beanie").tag("beanie")
            }
            Toggle("Glasses", isOn: $glasses).toggleStyle(.switch).controlSize(.small)
            Text("Change your look any day. Your selection stays saved until you change it.").font(.caption).foregroundStyle(Color.petMuted)
            Divider()
            Picker(SquipiLanguage.text("Language"), selection: $language) {
                ForEach(SquipiLanguage.released) { item in Text(item.label).tag(item.rawValue) }
            }
            Text("English is currently available for replies and speech. Other languages are hidden until their reply and speech quality has been verified.").font(.caption).foregroundStyle(Color.petMuted)
            if !speechSupport.isEmpty { Text(speechSupport).font(.caption).foregroundStyle(Color.petMuted) }
        }.card()
        .task(id: language) {
            let selected = SquipiLanguage.current
            let support = await AppleTranscriber.supported(language: selected)
            speechSupport = support == nil ? "On-device speech recognition is unavailable for \(selected.englishName). Typed chat still works." : "Speech recognition: \(selected.englishName), \(support!.dictation ? "Apple Dictation" : "Apple Speech"). The model may need a one-time download."
        }
        .onChange(of: language) { _, _ in app?.applyLanguagePref() }
        .onChange(of: colour) { _, _ in app?.applyLookPref(); NotificationCenter.default.post(name: .squipiAppearanceChanged, object: nil) }
        .onAppear { language = SquipiLanguage.current.rawValue; if ["scarf", "bowtie"].contains(outfit) { accessory = outfit; outfit = "classic"; app?.applyLookPref() } }
        .onChange(of: accessory) { _, _ in app?.applyLookPref() }
        .onChange(of: outfit) { _, _ in app?.applyLookPref() }
        .onChange(of: hat) { _, _ in app?.applyLookPref() }
        .onChange(of: glasses) { _, _ in app?.applyLookPref() }
    }
}
extension Notification.Name { static let squipiAppearanceChanged = Notification.Name("squipi.appearanceChanged") }

struct ResearchRunnerSettings: View {
    @AppStorage("nemoResearchEnabled") private var enabled = false
    @ObservedObject private var runner = NemoRunner.shared
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(text: "Research runner")
            Toggle("Use experimental NeMo research runner", isOn: $enabled)
            Text("Experimental; long template tasks can still pause before completion. Ollama only. Saves exact steps, continues recoverable timeouts and stops after repeated lack of progress. Say continue to resume a saved task after reopening Squipi. Web searches, reminders and cloud models use the standard runner.").font(.caption).foregroundStyle(Color.petMuted)
            HStack {
                Text(NemoRunner.available ? "Local runtime available" : "Local runtime needs setup").font(.caption)
                Spacer()
                Button(runner.installing ? "Setting up…" : "Set up NeMo") { Task { await runner.install() } }.buttonStyle(Pill(filled: false)).disabled(runner.installing)
            }
            if !runner.notice.isEmpty { Text(runner.notice).font(.caption).textSelection(.enabled) }
        }.card()
    }
}
