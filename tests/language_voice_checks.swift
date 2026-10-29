import Foundation
import AVFoundation
@main struct LanguageVoiceChecks {
    @MainActor static func main() {
        var count = 0
        precondition(SquipiLanguage.released == [.en])
        for code in ["en", "hi", "zh", "ar", "fr", "es", "unknown"] {
            precondition(SquipiLanguage.releasedSelection(code) == .en); count += 1
        }
        for language in SquipiLanguage.allCases {
            precondition(language.matches(locale: language.speechLocale)); count += 1
            precondition(!language.matches(locale: "de-DE")); count += 1
            guard let voice = PetVoice.voice(named: "Samantha", language: language) else {
                fatalError("No installed voice for \(language.rawValue); this machine cannot pass the voice selection check")
            }
            precondition(language.matches(locale: voice.language)); count += 1
            if language != .en { precondition(!voice.language.hasPrefix("en")); count += 1 }
            print("\(language.rawValue): \(voice.name) · \(voice.language)")
        }
        print("Passed \(count) language/voice checks, including no English fallback for non-English selections.")
    }
}
