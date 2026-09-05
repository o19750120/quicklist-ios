import SwiftUI

/// 純閱讀模式：不必正在播放也能看某一集的逐字稿。
///
/// 跟 `TranscriptView` 的差別是這裡沒有「跟著播放跑」這件事，
/// 所以不高亮、不自動捲、點句子也不會叫 Spotify 跳轉。
/// 取而代之的是一個書籤 —— 上次聽到的那句，進來就捲到那裡。
struct ReaderView: View {
    let entry: LibraryEntry

    @State private var transcript: Transcript?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var showTranslation = true
    @Environment(\.dismiss) private var dismiss

    private let service = SupabaseService()

    var body: some View {
        Group {
            if let transcript, !transcript.isEmpty {
                lineList(transcript)
            } else {
                placeholder
            }
        }
        .navigationTitle(entry.showName)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if transcript != nil {
                    Button {
                        showTranslation.toggle()
                    } label: {
                        Image(systemName: showTranslation
                              ? "character.book.closed.fill"
                              : "character.book.closed")
                    }
                    .tint(showTranslation ? Theme.accent : Theme.textSecondary)
                }
            }
        }
        .kikitoriBackground()
        .task { await load() }
    }

    // MARK: - 載入

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            transcript = try await service.fetchTranscript(spotifyEpisodeID: entry.episodeID)
            // 查過了就是準的，沒有逐字稿也要如實記成 0
            LibraryStore.shared.setLineCount(transcript?.lines.count ?? 0, for: entry.episodeID)
        } catch {
            errorMessage = error.localizedDescription
            logError("閱讀", "載入逐字稿失敗：\(error.localizedDescription)")
        }
    }

    // MARK: - 逐句列表

    private func lineList(_ transcript: Transcript) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.Space.lg) {
                    heading

                    LazyVStack(alignment: .leading, spacing: Theme.Space.xs) {
                        ForEach(transcript.lines) { line in
                            lineRow(line, isBookmark: line.id == bookmarkIndex(in: transcript))
                                .id(line.id)
                        }
                    }
                    Color.clear.frame(height: 60)
                }
                .padding(.horizontal, Theme.Space.xl)
                .padding(.top, Theme.Space.sm)
            }
            .task(id: transcript.episodeUUID) {
                // 進來就停在上次聽到的地方，像書籤一樣。
                // 要等 LazyVStack 先鋪好，不然捲到後段的句子會落空。
                guard let index = bookmarkIndex(in: transcript) else { return }
                try? await Task.sleep(nanoseconds: 200_000_000)
                guard !Task.isCancelled else { return }
                proxy.scrollTo(index, anchor: .center)
            }
        }
    }

    private var heading: some View {
        VStack(alignment: .leading, spacing: Theme.Space.sm) {
            Text(entry.episodeTitle)
                .font(.headline)
                .foregroundStyle(Theme.textPrimary)

            HStack(spacing: Theme.Space.sm) {
                Text("\(entry.lineCount) 句")
                Text("·")
                Text(entry.isFinished
                     ? "已聽完"
                     : "上次聽到 \(entry.lastPositionMs.asPlaybackTime)")
            }
            .font(.caption)
            .foregroundStyle(Theme.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.bottom, Theme.Space.xs)
    }

    /// 上次聽到的位置對應到哪一句。
    /// 書庫存的是 Spotify 的原始進度，逐字稿的時間軸則是原始音檔的，
    /// 兩者差一個對齊偏移，所以這裡要補回來。
    private func bookmarkIndex(in transcript: Transcript) -> Int? {
        guard entry.lastPositionMs > 0 else { return nil }
        let offset = AlignmentStore.load(for: entry.episodeID)
            ?? NowPlayingModel.guessOffsetMs(
                spotifyDurationMs: entry.durationMs,
                sourceDurationMs: transcript.sourceDurationMs
            )
            ?? 0
        return transcript.indexOfLine(at: max(0, entry.lastPositionMs + offset))
    }

    private func lineRow(_ line: TranscriptLine, isBookmark: Bool) -> some View {
        VStack(alignment: .leading, spacing: Theme.Space.xs) {
            HStack(alignment: .firstTextBaseline, spacing: Theme.Space.sm) {
                Text(line.startMs.asPlaybackTime)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 44, alignment: .leading)

                VStack(alignment: .leading, spacing: Theme.Space.xs) {
                    Text(line.text)
                        .font(.body)
                        .foregroundStyle(Theme.textPrimary)

                    if showTranslation, let translation = line.translation {
                        Text(translation)
                            .font(.footnote)
                            .foregroundStyle(Theme.textSecondary)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, Theme.Space.md)
        .padding(.horizontal, Theme.Space.md)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.md, style: .continuous)
                .fill(isBookmark ? Theme.surface : .clear)
        )
        .overlay(alignment: .leading) {
            if isBookmark {
                Capsule()
                    .fill(Theme.accent)
                    .frame(width: 3)
                    .padding(.vertical, Theme.Space.sm)
            }
        }
        .contextMenu {
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

    // MARK: - 沒東西可看的時候

    private var placeholder: some View {
        VStack(spacing: Theme.Space.lg) {
            if isLoading {
                ProgressView().tint(Theme.textSecondary)
                Text("載入逐字稿…")
                    .font(.footnote)
                    .foregroundStyle(Theme.textSecondary)
            } else {
                Image(systemName: "text.viewfinder")
                    .font(.system(size: Theme.heroIcon))
                    .foregroundStyle(Theme.textSecondary)
                Text("這一集還沒有逐字稿")
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Theme.textPrimary)
                Text("回到正在播放的畫面，按「產生逐字稿」就會排隊。")
                    .font(.caption)
                    .foregroundStyle(Theme.textSecondary)
                    .multilineTextAlignment(.center)
            }

            if let errorMessage {
                Text(errorMessage)
                    .font(.caption)
                    .foregroundStyle(Theme.accent)
                    .multilineTextAlignment(.center)
            }
        }
        .padding(.horizontal, Theme.Space.xxl)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
