import SwiftUI

struct NoteEditorView: View {
    @State private var draft: Note
    private let analytics = AnalyticsService()

    init(note: Note) { _draft = State(initialValue: note) }

    var body: some View {
        Form {
            TextField("Title", text: $draft.title)
            TextEditor(text: $draft.body)
            Button("Save") { Task { await save() } }
        }
    }

    /// The chain the graph makes explicit:
    /// NoteEditorView.save -> NoteStore.saveNote -> (table) notes
    func save() async {
        analytics.track("note_saved")
        try? await NoteStore.shared.saveNote(draft)
    }
}
