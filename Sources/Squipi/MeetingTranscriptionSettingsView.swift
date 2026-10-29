import SwiftUI

struct MeetingTranscriptionSettingsView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(text: "Meeting transcription")
            Text("Apple Speech · on this Mac")
                .font(.system(size: 12, weight: .medium, design: .rounded))
            Text("Apple Speech transcribes your microphone and call audio locally. Your saved Words vocabulary helps recognise personal and specialist terms. Recorded audio and transcripts stay on your Mac.")
                .foregroundStyle(Color.petMuted)
            Text("Apple Intelligence writes the meeting notes from that transcript. The transcript is saved first, so it remains available if note writing is interrupted or unavailable.")
                .foregroundStyle(Color.petMuted)
            Text("Microphone and call audio keep separate labels. Call participants share one label; individual speakers are not identified automatically.")
                .foregroundStyle(Color.petMuted)
        }
        .font(.system(size: 11.5, design: .rounded))
        .card()
    }
}
