import SwiftUI

/// 把 App 內部狀態攤開來看。
///
/// 每次上機測試都要接電腦重新匯入，沒有 Xcode console 可看，
/// 所以出問題時直接開這一頁截圖或複製，就能一次帶走足夠的資訊。
struct DiagnosticsView: View {
    @EnvironmentObject private var auth: SpotifyAuth
    @ObservedObject var model: NowPlayingModel
    @ObservedObject var transcriptModel: TranscriptModel
    @ObservedObject private var log = DebugLog.shared
    @Environment(\.dismiss) private var dismiss

    @State private var copied = false

    var body: some View {
        NavigationStack {
            List {
                Section("Spotify") {
                    row("授權狀態", auth.isAuthorized ? "已連接" : "未連接")
                    row("Client ID", auth.hasClientID ? "已設定（\(auth.clientID.count) 字元）" : "沒有")
                    row("輪詢中", model.isRunning ? "是" : "否")
                    if let error = auth.lastError {
                        row("最後錯誤", error, isError: true)
                    }
                }

                Section("目前播放") {
                    if let playing = model.nowPlaying {
                        row("類型", playing.kind.rawValue)
                        row("episode id", playing.id)
                        row("節目", playing.subtitle)
                        row("集數", playing.title)
                        row("進度", "\(model.displayProgressMs.asPlaybackTime) / \(playing.durationMs.asPlaybackTime)")
                        row("對齊偏移", String(format: "%+.2f 秒", Double(model.alignmentOffsetMs) / 1000))
                    } else {
                        row("狀態", model.statusMessage ?? "沒有播放中的內容")
                    }
                }

                Section("逐字稿") {
                    row("Supabase", transcriptModel.isConfigured ? "已注入金鑰" : "沒有金鑰", isError: !transcriptModel.isConfigured)
                    row("已載入", transcriptModel.hasTranscript ? "是（\(transcriptModel.transcript?.lines.count ?? 0) 句）" : "否")
                    if let job = transcriptModel.job {
                        row("任務狀態", job.status.label)
                        if let stage = job.stage, !stage.isEmpty { row("階段", stage) }
                        if let error = job.error, !error.isEmpty { row("錯誤", error, isError: true) }
                    }
                    if let index = transcriptModel.currentLineIndex {
                        row("目前句", "第 \(index + 1) 句")
                    }
                    if let error = transcriptModel.errorMessage {
                        row("讀取錯誤", error, isError: true)
                    }
                }

                Section {
                    if log.entries.isEmpty {
                        Text("還沒有紀錄")
                            .font(.footnote)
                            .foregroundStyle(Theme.textSecondary)
                    } else {
                        ForEach(log.entries) { entry in
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(spacing: 6) {
                                    Text(entry.timeText)
                                        .font(.caption2.monospacedDigit())
                                        .foregroundStyle(Theme.textSecondary)
                                    Text(entry.category)
                                        .font(.caption2.weight(.medium))
                                        .foregroundStyle(entry.isError ? Theme.accent : Theme.textSecondary)
                                }
                                Text(entry.message)
                                    .font(.caption)
                                    .foregroundStyle(entry.isError ? Theme.accent : Theme.textPrimary)
                            }
                            .padding(.vertical, 2)
                        }
                    }
                } header: {
                    HStack {
                        Text("執行紀錄")
                        Spacer()
                        Button(copied ? "已複製" : "複製全部") {
                            UIPasteboard.general.string = log.asPlainText
                            copied = true
                        }
                        .font(.caption)
                        .textCase(nil)
                        .disabled(log.entries.isEmpty)
                    }
                }
            }
            .navigationTitle("診斷")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("清除") { log.clear() }
                        .tint(Theme.textSecondary)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
            .scrollContentBackground(.hidden)
            .kikitoriBackground()
        }
    }

    private func row(_ label: String, _ value: String, isError: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(Theme.textSecondary)
            Text(value)
                .font(.footnote)
                .foregroundStyle(isError ? Theme.accent : Theme.textPrimary)
                .textSelection(.enabled)
        }
        .padding(.vertical, 1)
    }
}
