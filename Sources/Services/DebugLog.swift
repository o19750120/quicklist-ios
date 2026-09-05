import Foundation
import SwiftUI

/// 在 App 內留一份執行紀錄。
///
/// 這支 App 每次上機測試都要接電腦重新匯入，看不到 Xcode console，
/// 所以把關鍵事件記在 App 裡，透過診斷畫面直接讀，
/// 一次匯入就能回報足夠多的資訊。
@MainActor
final class DebugLog: ObservableObject {
    static let shared = DebugLog()

    struct Entry: Identifiable {
        let id = UUID()
        let time: Date
        let category: String
        let message: String
        let isError: Bool

        var timeText: String {
            let formatter = DateFormatter()
            formatter.dateFormat = "HH:mm:ss"
            return formatter.string(from: time)
        }
    }

    @Published private(set) var entries: [Entry] = []

    private let limit = 250

    private init() {}

    func write(_ category: String, _ message: String, isError: Bool = false) {
        entries.insert(
            Entry(time: Date(), category: category, message: message, isError: isError),
            at: 0
        )
        if entries.count > limit {
            entries.removeLast(entries.count - limit)
        }
    }

    func clear() {
        entries.removeAll()
    }

    var asPlainText: String {
        entries.reversed().map { entry in
            "\(entry.timeText) [\(entry.category)]\(entry.isError ? " 錯誤" : "") \(entry.message)"
        }.joined(separator: "\n")
    }
}

func logInfo(_ category: String, _ message: String) {
    Task { @MainActor in DebugLog.shared.write(category, message) }
}

func logError(_ category: String, _ message: String) {
    Task { @MainActor in DebugLog.shared.write(category, message, isError: true) }
}
