import Foundation

/// 記住每一集校正過的時間軸偏移。
///
/// Spotify 會在 podcast 插入廣告，害逐字稿的時間對不上。
/// 使用者校正過一次之後就該記起來，下次聽同一集不必再校。
enum AlignmentStore {
    private static let key = "kikitori.alignment_offsets"
    private static let limit = 200

    static func load(for episodeID: String) -> Int? {
        stored()[episodeID]
    }

    static func save(_ offsetMs: Int, for episodeID: String) {
        var map = stored()
        map[episodeID] = offsetMs

        // 存太多沒意義，超過上限就砍掉一半（字典本身無序，砍哪些不重要）
        if map.count > limit {
            for key in map.keys.prefix(map.count - limit / 2) {
                map.removeValue(forKey: key)
            }
        }
        UserDefaults.standard.set(map, forKey: key)
    }

    static func remove(for episodeID: String) {
        var map = stored()
        map.removeValue(forKey: episodeID)
        UserDefaults.standard.set(map, forKey: key)
    }

    private static func stored() -> [String: Int] {
        UserDefaults.standard.dictionary(forKey: key) as? [String: Int] ?? [:]
    }
}
