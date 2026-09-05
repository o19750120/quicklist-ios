import Foundation

/// 呼叫 Spotify Web API。目前只用到「現在播什麼」。
struct SpotifyAPI {

    enum APIError: LocalizedError {
        case notPlaying
        case rateLimited(retryAfter: Int)
        case http(Int, String)

        var errorDescription: String? {
            switch self {
            case .notPlaying:
                return "Spotify 目前沒有在播東西"
            case .rateLimited(let seconds):
                return "打太快了，\(seconds) 秒後再試"
            case .http(let code, let detail):
                return "Spotify 回應 \(code)：\(detail)"
            }
        }
    }

    let auth: SpotifyAuth

    /// 取得目前播放狀態。沒有在播（HTTP 204）時回 nil，這是正常情況不是錯誤。
    func fetchNowPlaying() async throws -> NowPlaying? {
        let token = try await auth.validAccessToken()

        var components = URLComponents(string: "https://api.spotify.com/v1/me/player")!
        // 不加這個參數的話，podcast 會被硬塞成 track 型別，拿不到節目資訊
        components.queryItems = [URLQueryItem(name: "additional_types", value: "episode")]

        var request = URLRequest(url: components.url!)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.cachePolicy = .reloadIgnoringLocalCacheData

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.http(0, "沒有收到 HTTP 回應")
        }

        switch http.statusCode {
        case 200:
            guard let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
                throw APIError.http(200, "回應不是 JSON")
            }
            return PlayerStateResponse(json: json)?.nowPlaying

        case 204:
            // 沒有任何裝置在播放
            return nil

        case 429:
            let retry = Int(http.value(forHTTPHeaderField: "Retry-After") ?? "5") ?? 5
            throw APIError.rateLimited(retryAfter: retry)

        default:
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let error = json?["error"] as? [String: Any]
            let message = error?["message"] as? String ?? "未知錯誤"
            throw APIError.http(http.statusCode, message)
        }
    }
}
