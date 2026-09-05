import Foundation

/// 逐字稿的一句。時間軸單位是毫秒，跟 Spotify 的 progress_ms 對齊。
struct TranscriptLine: Identifiable, Equatable {
    let id: Int
    let startMs: Int
    let endMs: Int
    let text: String
    let translation: String?

    func contains(_ positionMs: Int) -> Bool {
        positionMs >= startMs && positionMs < endMs
    }
}

struct Transcript: Equatable {
    let episodeUUID: String
    let showName: String
    let episodeTitle: String
    let language: String
    let lines: [TranscriptLine]

    var isEmpty: Bool { lines.isEmpty }

    /// 找出某個播放位置對應到哪一句。
    ///
    /// 用二分搜尋而不是逐句掃，因為這件事每 0.2 秒就要做一次，
    /// 一集三小時的節目可能有兩三千句。
    func indexOfLine(at positionMs: Int) -> Int? {
        guard !lines.isEmpty else { return nil }

        var low = 0
        var high = lines.count - 1
        var candidate: Int?

        while low <= high {
            let mid = (low + high) / 2
            let line = lines[mid]

            if line.contains(positionMs) {
                return mid
            } else if line.startMs > positionMs {
                high = mid - 1
            } else {
                // 這句已經講完了，先記著，繼續往後找更接近的
                candidate = mid
                low = mid + 1
            }
        }
        return candidate
    }
}

/// 轉錄任務的狀態，讓 App 知道「還沒好」是排隊中還是失敗了。
struct TranscriptJob: Equatable {
    enum Status: String {
        case queued
        case running
        case done
        case failed
        case unknown

        var label: String {
            switch self {
            case .queued: return "排隊中"
            case .running: return "轉錄中"
            case .done: return "已完成"
            case .failed: return "失敗"
            case .unknown: return "未知"
            }
        }
    }

    let id: String
    let status: Status
    let stage: String?
    let error: String?
}
