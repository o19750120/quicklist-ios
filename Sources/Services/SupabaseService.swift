import Foundation

/// 讀取後端產好的逐字稿，以及排隊請求轉錄。
///
/// App 只拿 anon key，資料表的 RLS 只開放讀取與新增任務，
/// 寫入逐字稿一律由 GitHub Actions 用 service key 執行。
struct SupabaseService {

    enum ServiceError: LocalizedError {
        case notConfigured
        case http(Int, String)

        var errorDescription: String? {
            switch self {
            case .notConfigured:
                return "尚未設定 Supabase（建置時沒有注入金鑰）"
            case .http(let code, let detail):
                return "Supabase 回應 \(code)：\(detail)"
            }
        }
    }

    var baseURL: String = BuildSecrets.supabaseURL
    var anonKey: String = BuildSecrets.supabaseAnonKey

    var isConfigured: Bool {
        !baseURL.isEmpty && !anonKey.isEmpty
    }

    private func request(_ path: String, method: String = "GET", body: Data? = nil) throws -> URLRequest {
        guard isConfigured, let url = URL(string: "\(baseURL)/rest/v1/\(path)") else {
            throw ServiceError.notConfigured
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue(anonKey, forHTTPHeaderField: "apikey")
        request.setValue("Bearer \(anonKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        request.cachePolicy = .reloadIgnoringLocalCacheData
        return request
    }

    private func send(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ServiceError.http(0, "沒有 HTTP 回應")
        }
        guard (200..<300).contains(http.statusCode) else {
            let detail = String(data: data, encoding: .utf8) ?? ""
            throw ServiceError.http(http.statusCode, String(detail.prefix(200)))
        }
        return data
    }

    // MARK: - 逐字稿

    /// 用 Spotify 的 episode id 查逐字稿。沒有就回 nil（代表還沒轉錄過）。
    func fetchTranscript(spotifyEpisodeID: String) async throws -> Transcript? {
        let select = "id,show_name,episode_title,language,kikitori_transcripts(lines,language)"
        let path = "kikitori_episodes?spotify_episode_id=eq.\(spotifyEpisodeID)&select=\(select)&limit=1"
        guard let encoded = path.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) else {
            return nil
        }

        let data = try await send(try request(encoded))
        guard let rows = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]],
              let row = rows.first else {
            return nil
        }

        // PostgREST 的巢狀結果可能是陣列也可能是物件
        let nested = row["kikitori_transcripts"]
        let transcriptDict: [String: Any]?
        if let array = nested as? [[String: Any]] {
            transcriptDict = array.first
        } else {
            transcriptDict = nested as? [String: Any]
        }

        guard let transcript = transcriptDict,
              let rawLines = transcript["lines"] as? [[String: Any]] else {
            return nil
        }

        let lines = rawLines.enumerated().compactMap { index, item -> TranscriptLine? in
            guard let text = item["text"] as? String, !text.isEmpty else { return nil }
            return TranscriptLine(
                id: index,
                startMs: item["start_ms"] as? Int ?? 0,
                endMs: item["end_ms"] as? Int ?? 0,
                text: text,
                translation: (item["translation"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            )
        }

        guard !lines.isEmpty else { return nil }

        return Transcript(
            episodeUUID: row["id"] as? String ?? "",
            showName: row["show_name"] as? String ?? "",
            episodeTitle: row["episode_title"] as? String ?? "",
            language: (transcript["language"] as? String) ?? (row["language"] as? String) ?? "ja",
            lines: lines
        )
    }

    // MARK: - 轉錄任務

    func fetchJob(spotifyEpisodeID: String) async throws -> TranscriptJob? {
        let path = "kikitori_jobs?spotify_episode_id=eq.\(spotifyEpisodeID)"
            + "&select=id,status,stage,error&order=created_at.desc&limit=1"
        guard let encoded = path.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) else {
            return nil
        }

        let data = try await send(try request(encoded))
        guard let rows = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]],
              let row = rows.first else {
            return nil
        }

        return TranscriptJob(
            id: row["id"] as? String ?? "",
            status: TranscriptJob.Status(rawValue: row["status"] as? String ?? "") ?? .unknown,
            stage: row["stage"] as? String,
            error: row["error"] as? String
        )
    }

    /// 請後端轉錄這一集。同一集重複排隊會被資料庫的唯一索引擋掉，
    /// 那不是錯誤，代表已經在排了。
    func enqueue(episode: NowPlaying) async throws {
        let payload: [String: Any] = [
            "spotify_episode_id": episode.id,
            "show_name": episode.subtitle,
            "episode_title": episode.title,
            "duration_ms": episode.durationMs
        ]
        let body = try JSONSerialization.data(withJSONObject: [payload])

        var post = try request("kikitori_jobs", method: "POST", body: body)
        post.setValue("return=minimal", forHTTPHeaderField: "Prefer")

        do {
            _ = try await send(post)
            logInfo("Supabase", "已排隊：\(episode.title)")
        } catch ServiceError.http(let code, let detail) where code == 409 {
            logInfo("Supabase", "這一集已經在排隊了")
            _ = detail
        }
    }
}
