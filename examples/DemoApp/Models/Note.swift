import Foundation

struct Note: Codable, Identifiable {
    let id: UUID
    var title: String
    var body: String
    var updatedAt: Date
}

struct Tag: Codable, Identifiable {
    let id: UUID
    var name: String
}
