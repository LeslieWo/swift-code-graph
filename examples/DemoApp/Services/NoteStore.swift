import Foundation

/// Talks to a Supabase-style REST backend.
/// The graph picks up `.from("notes")` and turns `notes` into a Table node.
final class NoteStore {
    static let shared = NoteStore()
    private let client = SupabaseClient()

    func loadNotes() async throws -> [Note] {
        try await client.from("notes").select().decoded()
    }

    func saveNote(_ note: Note) async throws {
        try await client.from("notes").upsert(note)
        try await touchSyncLog(for: note.id)
    }

    func deleteNote(id: UUID) async throws {
        try await client.from("notes").delete().eq("id", id)
    }

    func loadTags() async throws -> [Tag] {
        try await client.from("tags").select().decoded()
    }

    private func touchSyncLog(for id: UUID) async throws {
        try await client.from("sync_log").insert(["note_id": id.uuidString])
    }
}
