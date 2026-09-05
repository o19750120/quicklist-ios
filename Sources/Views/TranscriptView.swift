import SwiftUI

/// 逐句顯示原文與翻譯，跟著播放進度高亮並自動捲動。
struct TranscriptView: View {
    @ObservedObject var transcriptModel: TranscriptModel
    @ObservedObject var nowPlayingModel: NowPlayingModel
    let episode: NowPlaying

    /// 使用者手動捲動時暫停自動跟隨，免得跟他搶
    @State private var isUserScrolling = false
    @State private var autoFollowResumeTask: Task<Void, Never>?

    var body: some View {
        Group {
            if transcriptModel.hasTranscript {
                lineList
            } else {
                emptyState
            }
        }
    }

    // MARK: - 逐句列表

    private var lineList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 4) {
                    ForEach(transcriptModel.transcript?.lines ?? []) { line in
                        lineRow(line)
                            .id(line.id)
                    }
                    Color.clear.frame(height: 120)
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)
            }
            .simultaneousGesture(
                DragGesture().onChanged { _ in pauseAutoFollow() }
            )
            .onChange(of: transcriptModel.currentLineIndex) { index in
                guard let index, !isUserScrolling else { return }
                withAnimation(.easeInOut(duration: 0.35)) {
                    proxy.scrollTo(index, anchor: .center)
                }
            }
        }
    }

    private func lineRow(_ line: TranscriptLine) -> some View {
        let isCurrent = line.id == transcriptModel.currentLineIndex

        return VStack(alignment: .leading, spacing: 5) {
            Text(line.text)
                .font(isCurrent ? .title3.weight(.semibold) : .body)
                .foregroundStyle(isCurrent ? Theme.textPrimary : Theme.textSecondary)

            if transcriptModel.showTranslation, let translation = line.translation {
                Text(translation)
                    .font(isCurrent ? .subheadline : .footnote)
                    .foregroundStyle(isCurrent ? Theme.accent : Theme.textSecondary.opacity(0.7))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 10)
        .padding(.horizontal, 14)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(isCurrent ? Theme.surface : .clear)
        )
        .animation(.easeInOut(duration: 0.25), value: isCurrent)
        .contentShape(Rectangle())
        .onTapGesture {
            Task { await seek(to: line) }
        }
        .contextMenu {
            Button {
                nowPlayingModel.align(toLineStartMs: line.startMs)
                logInfo("對齊", "使用者指定第 \(line.id) 句，偏移 \(nowPlayingModel.alignmentOffsetMs) ms")
            } label: {
                Label("現在講的是這句", systemImage: "scope")
            }

            Button {
                UIPasteboard.general.string = line.text
            } label: {
                Label("複製原文", systemImage: "doc.on.doc")
            }

            if let translation = line.translation {
                Button {
                    UIPasteboard.general.string = translation
                } label: {
                    Label("複製翻譯", systemImage: "character.book.closed")
                }
            }
        }
    }

    /// 點某句就讓 Spotify 跳到那裡。需要 Premium，免費帳號會被擋。
    private func seek(to line: TranscriptLine) async {
        let target = max(0, line.startMs - nowPlayingModel.alignmentOffsetMs)
        await nowPlayingModel.seek(toMs: target)
    }

    private func pauseAutoFollow() {
        isUserScrolling = true
        autoFollowResumeTask?.cancel()
        autoFollowResumeTask = Task {
            try? await Task.sleep(nanoseconds: 6_000_000_000)
            guard !Task.isCancelled else { return }
            isUserScrolling = false
        }
    }

    // MARK: - 還沒有逐字稿

    private var emptyState: some View {
        VStack(spacing: 16) {
            if transcriptModel.isLoading {
                ProgressView()
                    .tint(Theme.textSecondary)
                Text("查詢逐字稿…")
                    .font(.footnote)
                    .foregroundStyle(Theme.textSecondary)

            } else if let job = transcriptModel.job, job.status == .queued || job.status == .running {
                ProgressView()
                    .tint(Theme.accent)
                Text(job.status == .queued ? "已排隊，等後端開始處理" : "轉錄中…")
                    .font(.subheadline)
                    .foregroundStyle(Theme.textPrimary)
                if let stage = job.stage, !stage.isEmpty {
                    Text(stage)
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                }
                Text("一集大約幾分鐘，好了會自動出現，可以先去聽")
                    .font(.caption)
                    .foregroundStyle(Theme.textSecondary)
                    .multilineTextAlignment(.center)

            } else if let job = transcriptModel.job, job.status == .failed {
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 34))
                    .foregroundStyle(Theme.accent)
                Text("轉錄失敗")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Theme.textPrimary)
                if let error = job.error, !error.isEmpty {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .multilineTextAlignment(.center)
                        .lineLimit(4)
                }
                requestButton(title: "再試一次")

            } else {
                Image(systemName: "text.viewfinder")
                    .font(.system(size: 38))
                    .foregroundStyle(Theme.textSecondary)
                Text("這一集還沒有逐字稿")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Theme.textPrimary)
                Text("後端會去找這一集的公開音檔，轉成逐句文字再翻譯。\n同一集只需要做一次。")
                    .font(.caption)
                    .foregroundStyle(Theme.textSecondary)
                    .multilineTextAlignment(.center)
                requestButton(title: "產生逐字稿")
            }

            if let error = transcriptModel.errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(Theme.accent)
                    .multilineTextAlignment(.center)
            }
        }
        .padding(.horizontal, 32)
        .padding(.vertical, 40)
        .frame(maxWidth: .infinity)
    }

    private func requestButton(title: String) -> some View {
        Button {
            Task { await transcriptModel.requestTranscription(for: episode) }
        } label: {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.black)
                .padding(.horizontal, 24)
                .padding(.vertical, 12)
                .background(Theme.accent, in: Capsule())
        }
        .padding(.top, 4)
    }
}
