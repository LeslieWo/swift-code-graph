import Foundation

/// Raw REST rather than a typed client. The `/rest/v1/<table>` rule catches this
/// shape too, which is the reason table detection is a regex pass and not AST-only.
final class AnalyticsService {
    func track(_ event: String) {
        guard let url = URL(string: "https://example.supabase.co/rest/v1/events") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.httpBody = try? JSONEncoder().encode(["name": event])
        URLSession.shared.dataTask(with: req).resume()
    }
}
