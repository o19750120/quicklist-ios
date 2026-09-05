import Foundation

/// 書庫：記住聽過哪些集、上次聽到哪。
///
/// 進度是每 0.2 秒推進一次的，但沒必要用那個頻率更新畫面或寫檔，
/// 所以這裡分成兩層節流：
/// - 位置變動超過 `positionGranularityMs` 才更新 `entries`（畫面才不會一直重繪）
/// - 距上次寫檔超過 `saveInterval` 才真的寫進 UserDefaults
///
/// 換集與 App 進背景時會強制寫一次，免得剛聽的那幾分鐘丟掉。
@MainActor
final class LibraryStore: ObservableObject {

    static let shared = LibraryStore()

    /// 依「最近聽過」排序
    @Published private(set) var entries: [LibraryEntry] = []

    private let key = "kikitori.library"
    private let limit = 300
    private let positionGranularityMs = 3_000
    private let saveInterval: TimeInterval = 15

    private var lastSavedAt = Date.distantPast

    private init() {
        entries = loadFromDisk()
    }

    // MARK: - 查詢

    func entry(for episodeID: String) -> LibraryEntry? {
        entries.first { $0.episodeID == episodeID }
    }

    /// 依節目分組，組內依最近收聽排序，組跟組之間也是。
    var groupedByShow: [(show: String, entries: [LibraryEntry])] {
        var order: [String] = []
        var buckets: [String: [LibraryEntry]] = [:]

        for entry in entries {
            let show = entry.showName.isEmpty ? "未知節目" : entry.showName
            if buckets[show] == nil {
                buckets[show] = []
                order.append(show)
            }
            buckets[show]?.append(entry)
        }
        return order.map { (show: $0, entries: buckets[$0] ?? []) }
    }

    // MARK: - 記錄

    /// 正在播的時候持續呼叫。內部會自己節流，呼叫端不用管頻率。
    ///
    /// - Parameters:
    ///   - positionMs: Spotify 的原始進度，不要加對齊偏移
    ///   - lineCount: 逐字稿句數，還沒有就傳 0
    func record(episode: NowPlaying, positionMs: Int, lineCount: Int) {
        guard episode.kind == .episode, !episode.id.isEmpty else { return }

        let existing = entry(for: episode.id)

        // 沒有實質變化就不動，否則畫面每 0.2 秒重繪一次
        if let existing,
           abs(existing.lastPositionMs - positionMs) < positionGranularityMs,
           existing.lineCount == lineCount,
           existing.durationMs == episode.durationMs {
            return
        }

        let updated = LibraryEntry(
            episodeID: episode.id,
            showName: episode.subtitle,
            episodeTitle: episode.title,
            artworkURL: episode.artworkURL?.absoluteString ?? existing?.artworkURL,
            durationMs: episode.durationMs > 0 ? episode.durationMs : (existing?.durationMs ?? 0),
            lastPositionMs: positionMs,
            // 逐字稿還沒載入時傳 0，別把已知的句數蓋掉
            lineCount: lineCount > 0 ? lineCount : (existing?.lineCount ?? 0),
            lastListenedAt: Date()
        )

        entries.removeAll { $0.episodeID == episode.id }
        entries.insert(updated, at: 0)

        if entries.count > limit {
            entries = Array(entries.prefix(limit))
        }

        // 換集是「這一集聽完了」的時刻，值得立刻落地
        let switchedEpisode = existing == nil
        saveIfNeeded(force: switchedEpisode)
    }

    /// 明確查過後端之後才呼叫，所以 0 也算數 —— 那代表這一集真的沒有逐字稿。
    ///
    /// 跟 `record` 裡的處理不同：那邊的 0 只代表「還沒載入」，不能拿來覆蓋。
    func setLineCount(_ lineCount: Int, for episodeID: String) {
        guard let index = entries.firstIndex(where: { $0.episodeID == episodeID }),
              entries[index].lineCount != lineCount else { return }

        entries[index].lineCount = lineCount
        saveIfNeeded(force: true)
    }

    func remove(episodeID: String) {
        entries.removeAll { $0.episodeID == episodeID }
        saveIfNeeded(force: true)
    }

    func removeAll() {
        entries = []
        saveIfNeeded(force: true)
    }

    /// App 要進背景了，把還沒落地的寫下去
    func flush() {
        saveIfNeeded(force: true)
    }

    // MARK: - 存取

    private func saveIfNeeded(force: Bool) {
        guard force || Date().timeIntervalSince(lastSavedAt) >= saveInterval else { return }
        lastSavedAt = Date()

        do {
            let data = try JSONEncoder().encode(entries)
            UserDefaults.standard.set(data, forKey: key)
        } catch {
            logError("書庫", "寫入失敗：\(error.localizedDescription)")
        }
    }

    private func loadFromDisk() -> [LibraryEntry] {
        guard let data = UserDefaults.standard.data(forKey: key) else { return [] }
        do {
            return try JSONDecoder().decode([LibraryEntry].self, from: data)
        } catch {
            logError("書庫", "讀取失敗，當成空的：\(error.localizedDescription)")
            return []
        }
    }
}
