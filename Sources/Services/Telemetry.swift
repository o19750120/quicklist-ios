import Foundation
import UIKit

/// 把 App 在裝置上發生的事回報到 Supabase。
///
/// 這支 App 每次上機都要接電腦重新匯入，沒有 Xcode console 可看，
/// 出問題只能靠使用者截圖轉述。有了這個，開發時就能直接查裝置上到底發生什麼。
///
/// 只送技術事件：錯誤、狀態轉換、API 回應碼。
/// 不送逐字稿內容、不送金鑰、不送任何能識別本人的東西
/// （device_id 是首次啟動隨機產生的，跟裝置識別碼無關）。
@MainActor
final class Telemetry {
    static let shared = Telemetry()

    private var pending: [[String: Any]] = []
    private var flushTimer: Task<Void, Never>?
    private var isFlushing = false

    private let batchLimit = 20
    private let flushInterval: UInt64 = 60_000_000_000  // 60 秒

    private let deviceID: String
    private let appVersion: String

    private var baseURL: String { BuildSecrets.supabaseURL }
    private var anonKey: String { BuildSecrets.supabaseAnonKey }
    private var isConfigured: Bool { !baseURL.isEmpty && !anonKey.isEmpty }

    private init() {
        // 隨機碼存在本機，重裝就換一個。只用來把同一次安裝的紀錄串起來。
        let key = "kikitori.device_id"
        if let existing = UserDefaults.standard.string(forKey: key) {
            deviceID = existing
        } else {
            let generated = UUID().uuidString
            UserDefaults.standard.set(generated, forKey: key)
            deviceID = generated
        }

        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?"
        appVersion = "\(version) (\(build))"
    }

    func start() {
        guard isConfigured, flushTimer == nil else { return }

        record(level: "info", category: "App", message: "啟動",
               context: ["ios": UIDevice.current.systemVersion,
                         "model": UIDevice.current.model])

        flushTimer = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: self?.flushInterval ?? 60_000_000_000)
                await self?.flush()
            }
        }
    }

    func record(level: String, category: String, message: String, context: [String: Any]? = nil) {
        guard isConfigured else { return }

        var row: [String: Any] = [
            "device_id": deviceID,
            "app_version": appVersion,
            "level": level,
            "category": category,
            "message": String(message.prefix(1000)),
        ]
        if let context, let data = try? JSONSerialization.data(withJSONObject: context),
           let json = String(data: data, encoding: .utf8) {
            row["context"] = json
        }

        pending.append(row)

        // 錯誤不等排程，立刻送出，免得 App 當掉就沒了
        if level == "error" || pending.count >= batchLimit {
            Task { await flush() }
        }
    }

    func flush() async {
        guard isConfigured, !isFlushing, !pending.isEmpty else { return }

        isFlushing = true
        let batch = pending
        pending = []
        defer { isFlushing = false }

        guard let url = URL(string: "\(baseURL)/rest/v1/kikitori_logs"),
              let body = try? JSONSerialization.data(withJSONObject: batch) else {
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue(anonKey, forHTTPHeaderField: "apikey")
        request.setValue("Bearer \(anonKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("return=minimal", forHTTPHeaderField: "Prefer")
        request.httpBody = body

        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                // 回報失敗就不再重試，免得無限迴圈；本地紀錄還在，不影響使用
                pending.insert(contentsOf: batch.prefix(batchLimit), at: 0)
            }
        } catch {
            pending.insert(contentsOf: batch.prefix(batchLimit), at: 0)
        }
    }
}
