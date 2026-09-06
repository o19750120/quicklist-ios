import SwiftUI

/// 逐句顯示原文與翻譯，跟著播放進度高亮並自動捲動。
struct TranscriptView: View {
    @ObservedObject var transcriptModel: TranscriptModel
    @ObservedObject var nowPlayingModel: NowPlayingModel
    let episode: NowPlaying

    /// 點開的那個詞。日文沒有空格，能點是因為後端把詞邊界一起算好了。
    @State private var lookup: WordLookup?
    /// 使用者手動捲動時暫停自動跟隨，免得跟他搶
    @State private var isUserScrolling = false
    @State private var autoFollowResumeTask: Task<Void, Never>?

    var body: some View {
        Group {
            if transcriptModel.hasTranscript {
                lineList
                    // 用 safeAreaInset 而不是 overlay：讓逐字稿自己讓出這塊寬度，
                    // 不然控制條會壓在字上
                    .safeAreaInset(edge: .trailing, spacing: 0) {
                        SideAssistant(
                            interaction: $transcriptModel.interaction,
                            canLookUpWords: transcriptModel.transcript?.canLookUpWords ?? false,
                            repeatsCurrentLine: transcriptModel.repeatsCurrentLine,
                            onToggleRepeat: { transcriptModel.toggleRepeat() }
                        )
                    }
            } else {
                emptyState
            }
        }
    }

    // MARK: - 逐句列表

    private var lineList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Theme.Space.xs) {
                    ForEach(transcriptModel.transcript?.lines ?? []) { line in
                        lineRow(line)
                            .id(line.id)
                    }
                    Color.clear.frame(height: 120)
                }
                .padding(.horizontal, Theme.Space.xl)
                .padding(.top, Theme.Space.sm)
            }
            .simultaneousGesture(
                DragGesture().onChanged { _ in pauseAutoFollow() }
            )
            .sheet(item: $lookup) { item in
                WordDetailView(
                    word: item.word,
                    reading: item.reading,
                    isAmbiguous: item.isAmbiguous,
                    entry: transcriptModel.transcript?.vocabulary?.entry(for: item.lemma)
                )
                .presentationDetents([.medium, .large])
            }
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

        let spans = transcriptModel.transcript?.vocabulary?.spans(forLine: line.id) ?? []

        return VStack(alignment: .leading, spacing: Theme.Space.xs) {
            if spans.isEmpty {
                // 舊資料沒有詞表，就是一整句不能點
                Text(line.text)
                    .font(isCurrent ? .title3.weight(.semibold) : .body)
                    .foregroundStyle(isCurrent ? Theme.textPrimary : Theme.textSecondary)
            } else {
                wordFlow(line, spans: spans, isCurrent: isCurrent)
            }

            if transcriptModel.showTranslation, let translation = line.translation {
                Text(translation)
                    .font(isCurrent ? .subheadline : .footnote)
                    .foregroundStyle(isCurrent ? Theme.accent : Theme.textSecondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, Theme.Space.md)
        .padding(.horizontal, Theme.Space.lg)
        .overlay(alignment: .leading) { speakerMark(for: line) }
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.md, style: .continuous)
                .fill(isCurrent ? Theme.surface : .clear)
                // 動畫只掛在背景上。掛在整個 row 上的話，
                // 長按選單的預覽會跟著高亮狀態一起重繪而閃爍。
                .animation(.easeInOut(duration: 0.25), value: isCurrent)
        )
        .contentShape(Rectangle())
        .accessibilityIdentifier("transcript.line.\(line.id)")
        .onTapGesture {
            // 查詞模式下點到詞以外的地方（助詞、標點）不做事 ——
            // 那時使用者是在讀，不是要跳轉
            guard transcriptModel.interaction == .playback else { return }
            Task { await seek(to: line) }
        }
        .contextMenu {
            Button {
                nowPlayingModel.align(toLineStartMs: line.startMs)
                logInfo("對齊", "手動指定第 \(line.id + 1) 句，偏移 \(nowPlayingModel.alignmentOffsetMs) ms")
            } label: {
                Label("現在講的是這句", systemImage: "scope")
            }

            Button {
                Task { await seek(to: line) }
            } label: {
                Label("從這裡開始播", systemImage: "play.circle")
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
        } preview: {
            // 給一個靜態預覽，選單開著時就不會被外面的狀態變化牽動
            VStack(alignment: .leading, spacing: Theme.Space.sm) {
                Text(line.text)
                    .font(.body)
                    .foregroundStyle(Theme.textPrimary)
                if let translation = line.translation {
                    Text(translation)
                        .font(.footnote)
                        .foregroundStyle(Theme.textSecondary)
                }
                Text(line.startMs.asPlaybackTime)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(Theme.textSecondary)
            }
            .padding(Theme.Space.lg)
            .frame(maxWidth: 340, alignment: .leading)
            .background(Theme.surface)
        }
    }

    // MARK: - 可以點的詞

    /// 一句話拆成一個個詞排出來。
    ///
    /// 點到詞就查字典，點到助詞、標點或空白處還是跳轉 ——
    /// 兩種操作落在不同的地方，不必另外開一個模式切換。
    private func wordFlow(_ line: TranscriptLine,
                          spans: [Vocabulary.Span],
                          isCurrent: Bool) -> some View {
        let characters = Array(line.text)
        let pieces = merged(spans, in: characters)

        return WordFlowLayout(lineSpacing: isCurrent ? 8 : 6) {
            ForEach(Array(pieces.enumerated()), id: \.offset) { index, item in
                let span = item.span
                let piece = item.text
                Text(piece)
                    .font(isCurrent ? .title3.weight(.semibold) : .body)
                    .foregroundStyle(isCurrent ? Theme.textPrimary : Theme.textSecondary)
                    // 後端判定這裡有兩種讀法時給個很輕的提示，
                    // 不是每個「字典裡有別的讀音」的詞都標 —— 那會有三分之一的詞中標。
                    .overlay(alignment: .bottom) {
                        if span.isAmbiguous {
                            Rectangle()
                                .fill(Theme.accent.opacity(0.5))
                                .frame(height: 1)
                        }
                    }
                    .contentShape(Rectangle())
                    // 只有查得到的詞掛識別名，UI 測試才點得準，
                    // 也才不會誤點到助詞或上方資訊列的節目名
                    .accessibilityIdentifier(
                        span.lemma == nil ? "transcript.token" : "transcript.word.\(line.id).\(index)"
                    )
                    .onTapGesture {
                        // 播放模式下讓事件落到整列的手勢上，點哪裡都是跳轉
                        guard transcriptModel.interaction == .lookup else {
                            Task { await seek(to: line) }
                            return
                        }
                        guard let lemma = span.lemma else { return }
                        lookup = WordLookup(
                            word: piece,
                            lemma: lemma,
                            reading: transcriptModel.transcript?.vocabulary?.reading(for: span),
                            isAmbiguous: span.isAmbiguous
                        )
                    }
            }
        }
    }

    /// 日文的禁則：句號、逗號、收尾括號不能出現在行首。
    /// 後端把標點切成獨立的一段，照那樣排就會看到「。」自己掉到下一行。
    private static let cannotStartLine: Set<Character> = [
        "。", "、", "，", "．", "」", "』", "）", "〉", "》", "！", "？", "…", "ー", "・",
        ".", ",", ")", "]", "}", "!", "?",
    ]

    /// 把不能放在行首的標點併進前一段，換行才不會把它孤零零留在下一行的開頭。
    private func merged(_ spans: [Vocabulary.Span],
                        in characters: [Character]) -> [(span: Vocabulary.Span, text: String)] {
        var result: [(span: Vocabulary.Span, text: String)] = []

        for span in spans {
            let piece = text(of: span, in: characters)
            guard !piece.isEmpty else { continue }

            let isTrailingPunctuation = span.lemma == nil
                && piece.allSatisfy { Self.cannotStartLine.contains($0) }

            if isTrailingPunctuation, let last = result.last {
                // 併進前一段，可點的範圍還是原本那個詞
                result[result.count - 1] = (last.span, last.text + piece)
            } else {
                result.append((span, piece))
            }
        }
        return result
    }

    private func text(of span: Vocabulary.Span, in characters: [Character]) -> String {
        guard span.start >= 0, span.end <= characters.count, span.start < span.end else {
            return ""
        }
        return String(characters[span.start..<span.end])
    }

    // MARK: - 說話者

    /// 換人講話時在句子左緣畫一條細線。
    ///
    /// 刻意不用背景色也不放頭像：這幾集實測平均每三句就換一次人，
    /// 背景色會讓畫面一直閃、還會跟「目前這句」的高亮打架，
    /// 而那是這個 App 最重要的視覺訊號。diarization 本身也會出錯，
    /// 細線的份量剛好對應它的可信度。
    @ViewBuilder
    private func speakerMark(for line: TranscriptLine) -> some View {
        if let transcript = transcriptModel.transcript,
           transcript.startsNewSpeaker(at: line.id),
           let speaker = line.speaker {
            Capsule()
                .fill(speakerColor(speaker))
                .frame(width: 3)
                .padding(.vertical, Theme.Space.sm)
        }
    }

    /// 說話者的顏色只從中性色階取，不用彩色 —— 橘色是「目前這句」的專用訊號。
    private func speakerColor(_ speaker: Int) -> Color {
        let shades: [Color] = [Theme.textSecondary, Theme.surfaceRaised, Theme.textPrimary]
        return shades[abs(speaker) % shades.count]
    }

    /// 點某句就讓 Spotify 跳到那裡。需要 Premium，免費帳號會被擋。
    private func seek(to line: TranscriptLine) async {
        // 開著逐句重聽的時候自己點了別句，就換成重聽那一句
        transcriptModel.moveRepeat(to: line.id)
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
        VStack(spacing: Theme.Space.lg) {
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
                Text(queueEstimate)
                    .font(.caption)
                    .foregroundStyle(Theme.textSecondary)
                    .multilineTextAlignment(.center)

            } else if let job = transcriptModel.job, job.status == .failed {
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: Theme.heroIcon))
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
                    .font(.system(size: Theme.heroIcon))
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
        .padding(.horizontal, Theme.Space.xxl)
        .padding(.vertical, Theme.Space.xxl)
        // 要撐滿剩下的高度，否則上方的資訊列會被 VStack 垂直置中推到畫面中間
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// 等多久，依這一集多長來講。
    ///
    /// 主要成本在讀音覆核，而它會撞 API 配額 —— 40 分鐘的節目實測要十分鐘左右，
    /// 11 分鐘的只要兩分半。用同一句「大約幾分鐘」對長節目是騙人的，
    /// 使用者會以為卡住了。
    private var queueEstimate: String {
        let minutes = episode.durationMs / 60_000
        guard minutes >= 20 else {
            return "大約兩三分鐘，好了會自動出現，可以先去聽。"
        }
        return "這一集有 \(minutes) 分鐘，長節目大約要等十分鐘。\n好了會自動出現，可以先去聽。"
    }

    private func requestButton(title: String) -> some View {
        Button {
            Task { await transcriptModel.requestTranscription(for: episode) }
        } label: {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.black)
                .padding(.horizontal, Theme.Space.xl)
                .padding(.vertical, Theme.Space.md)
                .background(Theme.accent, in: Capsule())
        }
        .padding(.top, Theme.Space.xs)
    }
}
