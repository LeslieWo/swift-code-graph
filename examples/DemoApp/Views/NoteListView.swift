import SwiftUI

struct NoteListView: View {
    @StateObject private var model = NoteListViewModel()

    var body: some View {
        List(model.notes) { note in
            NoteRow(note: note)
        }
        .task { await model.refresh() }
    }
}

struct NoteRow: View {
    let note: Note
    var body: some View { Text(note.title) }
}

@MainActor
final class NoteListViewModel: ObservableObject {
    @Published var notes: [Note] = []
    private let analytics = AnalyticsService()

    func refresh() async {
        analytics.track("notes_opened")
        notes = (try? await NoteStore.shared.loadNotes()) ?? []
    }

    func delete(_ note: Note) async {
        try? await NoteStore.shared.deleteNote(id: note.id)
        await refresh()
    }
}
