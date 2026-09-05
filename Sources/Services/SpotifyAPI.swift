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

    /// 把播放位置跳到指定毫秒。逐句重聽靠這個。
    /// 控制播放需要 Premium，免費帳號會收到 403。
    func seek(toMs positionMs: Int) async throws {
        let token = try await auth.validAccessToken()

        var components = URLComponents(string: "https://api.spotify.com/v1/me/player/seek")!
        components.queryItems = [
            URLQueryItem(name: "position_ms", value: String(max(0, positionMs)))
        ]

        var request = URLRequest(url: components.url!)
        request.httpMethod = "PUT"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("0", forHTTPHeaderField: "Content-Length")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.http(0, "沒有收到 HTTP 回應")
        }

        switch http.statusCode {
        case 200, 202, 204:
            return
        case 403:
            throw APIError.http(403, "跳轉需要 Spotify Premium")
        case 404:
            throw APIError.http(404, "找不到播放中的裝置，先在 Spotify 按播放")
        default:
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let error = json?["error"] as? [String: Any]
            throw APIError.http(http.statusCode, error?["message"] as? String ?? "跳轉失敗")
        }
    }
}
