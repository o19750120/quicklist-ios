import SwiftUI

/// 主畫面：上方顯示 Spotify 正在播什麼，下方是跟著跑的逐字稿。
struct NowPlayingView: View {
    @EnvironmentObject private var auth: SpotifyAuth
    @ObservedObject var model: NowPlayingModel
    @StateObject private var transcriptModel = TranscriptModel()

    @State private var showSettings = false
    @State private var showDiagnostics = false
    @State private var showLibrary = false
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            Group {
                if let playing = model.nowPlaying {
                    Group {
                        if playing.kind == .episode {
                            TranscriptView(
                                transcriptModel: transcriptModel,
                                nowPlayingModel: model,
                                episode: playing
                            )
                        } else {
                            notAPodcast
                        }
                    }
                    // 正在播放的資訊放在底部，像音樂 App 的 mini player。
                    // 逐字稿因此從畫面最上面就開始，閱讀的那一段不被切掉；
                    // 進度與集名放在拇指旁邊，要看的時候低頭就有。
                    .safeAreaInset(edge: .bottom, spacing: 0) {
                        header(playing)
                            .background(.ultraThinMaterial)
                    }
                } else {
                    idleState
                }
            }
            .navigationTitle("Kikitori")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
            .kikitoriBackground()
        }
        .sheet(isPresented: $showSettings) {
            SettingsView(model: model).environmentObject(auth)
        }
        .sheet(isPresented: $showLibrary) {
            LibraryView(model: model)
        }
        .sheet(isPresented: $showDiagnostics) {
            DiagnosticsView(model: model, transcriptModel: transcriptModel)
                .environmentObject(auth)
        }
        .task {
            model.start()
        }
        .onDisappear {
            model.stop()
            transcriptModel.stopPolling()
        }
        .onChange(of: model.nowPlaying?.id) { newID in
            guard let newID, model.nowPlaying?.kind == .episode else { return }
            Task {
                await transcriptModel.loadIfNeeded(episodeID: newID)
                // 逐字稿到手才知道原始音檔多長，才能推估偏移
                model.applyAlignment(episodeID: newID, transcript: transcriptModel.transcript)
            }
        }
        .onChange(of: model.displayProgressMs) { _ in
            transcriptModel.updatePosition(model.alignedProgressMs)
            recordToLibrary()

            // 逐句重聽：播到這一句的結尾就跳回句首
            if let line = transcriptModel.lineToRepeat(at: model.alignedProgressMs) {
                Task {
                    await model.seek(toMs: max(0, line.startMs - model.alignmentOffsetMs))
                }
            }
        }
        .onChange(of: transcriptModel.transcript) { transcript in
            // 逐字稿比播放晚一步到，到了才補記句數。
            // 用 loadedEpisodeID 而不是現在播的那一集 —— 查詢期間可能已經換集了。
            guard let id = transcriptModel.loadedEpisodeID else { return }
            LibraryStore.shared.setLineCount(transcript?.lines.count ?? 0, for: id)
        }
        .onChange(of: scenePhase) { phase in
            // 從 Spotify 切回來時立刻對一次時間，不必等下一輪輪詢
            if phase == .active {
                Task { await model.refresh() }
            }
        }
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button {
                showLibrary = true
            } label: {
                Image(systemName: "books.vertical")
            }
            .tint(Theme.textSecondary)
            .accessibilityIdentifier("toolbar.library")
        }

        ToolbarItem(placement: .topBarLeading) {
            if transcriptModel.hasTranscript {
                Button {
                    transcriptModel.showTranslation.toggle()
                } label: {
                    Image(systemName: transcriptModel.showTranslation
                          ? "character.book.closed.fill"
                          : "character.book.closed")
                }
                .tint(transcriptModel.showTranslation ? Theme.accent : Theme.textSecondary)
            }
        }

        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                Button {
                    showSettings = true
                } label: {
                    Label("設定", systemImage: "gearshape")
                }
                Button {
                    showDiagnostics = true
                } label: {
                    Label("診斷", systemImage: "stethoscope")
                }
                if model.alignmentOffsetMs != 0 {
                    Button {
                        model.resetAlignment()
                    } label: {
                        Label("清除對齊偏移", systemImage: "arrow.counterclockwise")
                    }
                }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .tint(Theme.textSecondary)
        }
    }

    /// 把「聽到哪」寫進書庫。LibraryStore 自己會節流，這裡照呼叫沒關係。
    private func recordToLibrary() {
        guard let playing = model.nowPlaying, playing.kind == .episode else { return }
        LibraryStore.shared.record(
            episode: playing,
            positionMs: model.displayProgressMs,
            lineCount: transcriptModel.lineCount(for: playing.id)
        )
    }

    // MARK: - 上方資訊列

    private func header(_ playing: NowPlaying) -> some View {
        VStack(spacing: Theme.Space.md) {
            HStack(spacing: Theme.Space.lg) {
                AsyncImage(url: playing.artworkURL) { phase in
                    if case .success(let image) = phase {
                        image.resizable().aspectRatio(contentMode: .fill)
                    } else {
                        Theme.surfaceRaised
                    }
                }
                .frame(width: 56, height: 56)
                .clipShape(RoundedRectangle(cornerRadius: Theme.Radius.sm, style: .continuous))

                VStack(alignment: .leading, spacing: 3) {
                    Text(playing.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.textPrimary)
                        .lineLimit(2)

                    HStack(spacing: Theme.Space.xs) {
                        Circle()
                            .fill(statusColor(playing))
                            .frame(width: 6, height: 6)
                        Text(statusText(playing))
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)
                        Text("·")
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary.opacity(0.5))
                        Text(playing.subtitle)
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)
                            .lineLimit(1)
                    }
                }

                Spacer(minLength: 0)
            }

            VStack(spacing: Theme.Space.sm) {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Theme.surfaceRaised)
                        Capsule()
                            .fill(Theme.accent)
                            .frame(width: geo.size.width * progressFraction(playing))
                    }
                }
                .frame(height: 4)

                HStack {
                    Text(model.displayProgressMs.asPlaybackTime)
                    if model.alignmentOffsetMs != 0 {
                        Text(offsetLabel)
                            .foregroundStyle(Theme.accent)
                    }
                    Spacer()
                    Text(playing.durationMs.asPlaybackTime)
                }
                .font(.caption2.monospacedDigit())
                .foregroundStyle(Theme.textSecondary)
            }
        }
        .padding(.horizontal, Theme.Space.xl)
        .padding(.top, Theme.Space.sm)
        .padding(.bottom, Theme.Space.lg)
    }

    private var offsetLabel: String {
        String(format: "校正 %+.1fs", Double(model.alignmentOffsetMs) / 1000)
    }

    /// Spotify 暫停久了會回報「沒有裝置在播」，那時畫面留在最後一集，
    /// 不是錯誤，所以用不同的顏色與說法區分開。
    private func statusColor(_ playing: NowPlaying) -> Color {
        if model.isOffline { return Theme.accent }
        if playing.isPlaying { return Theme.spotifyGreen }
        return model.isDisconnected ? Theme.textSecondary : Theme.accent
    }

    private func statusText(_ playing: NowPlaying) -> String {
        // 離線時進度是停的，說「播放中」會騙人
        if model.isOffline { return "離線，等網路回來" }
        if playing.isPlaying { return "播放中" }
        return model.isDisconnected ? "Spotify 已閒置" : "已暫停"
    }

    private func progressFraction(_ playing: NowPlaying) -> Double {
        guard playing.durationMs > 0 else { return 0 }
        return min(1, max(0, Double(model.displayProgressMs) / Double(playing.durationMs)))
    }

    // MARK: - 其他狀態

    private var notAPodcast: some View {
        VStack(spacing: Theme.Space.lg) {
            Image(systemName: "music.note")
                .font(.system(size: Theme.heroIcon))
                .foregroundStyle(Theme.textSecondary)
            Text("現在播的是歌曲")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.textPrimary)
            Text("Kikitori 是為 podcast 做的。\n去 Spotify 播一集節目再回來。")
                .font(.caption)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var idleState: some View {
        VStack(spacing: Theme.Space.lg) {
            Image(systemName: idleIcon)
                .font(.system(size: Theme.heroIcon))
                .foregroundStyle(idleTint)

            Text(idleTitle)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.textPrimary)

            Text(idleDetail)
                .font(.caption)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, Theme.Space.xxl)

            idleActions
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// 「打開 App 就接上」的關鍵：沒在播的時候，不必切去 Spotify 按播放，
    /// 在這裡按一下就好。只有 Spotify 那頭完全沒有裝置時才需要真的切過去。
    @ViewBuilder
    private var idleActions: some View {
        if case .nothingPlaying = model.idleState {
            VStack(spacing: Theme.Space.md) {
                Button {
                    Task { await model.resumePlayback() }
                } label: {
                    Label("接著播", systemImage: "play.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.black)
                        .padding(.horizontal, Theme.Space.xl)
                        .padding(.vertical, Theme.Space.md)
                        .background(Theme.spotifyGreen, in: Capsule())
                }

                Button("打開 Spotify") {
                    if let url = URL(string: "spotify://") {
                        openURL(url)
                    }
                }
                .font(.caption)
                .tint(Theme.textSecondary)
            }
        } else {
            Button {
                Task { await model.refresh() }
            } label: {
                Text("重新整理")
                    .font(.subheadline.weight(.medium))
                    .padding(.horizontal, Theme.Space.xl)
                    .padding(.vertical, Theme.Space.md)
                    .background(Theme.surfaceRaised, in: Capsule())
            }
            .tint(Theme.textPrimary)
        }
    }

    // 三種「沒東西可看」要做的事完全不同，所以分開講

    private var idleIcon: String {
        switch model.idleState {
        case .connecting:     return "waveform"
        case .offline:        return "wifi.slash"
        case .nothingPlaying: return "pause.circle"
        case .failed:         return "exclamationmark.triangle"
        }
    }

    private var idleTint: Color {
        switch model.idleState {
        case .connecting, .nothingPlaying: return Theme.textSecondary
        case .offline, .failed:            return Theme.accent
        }
    }

    private var idleTitle: String {
        switch model.idleState {
        case .connecting:     return "正在跟 Spotify 對狀態"
        case .offline:        return "沒有網路"
        case .nothingPlaying: return "Spotify 現在沒有在播"
        case .failed:         return "連不上 Spotify"
        }
    }

    private var idleDetail: String {
        switch model.idleState {
        case .connecting:
            return "馬上就好。"
        case .offline:
            return "連上網路就會自己接回來，不用重開 App。\n書庫裡讀過的逐字稿不受影響。"
        case .nothingPlaying:
            return "去 Spotify 播一集 podcast，再回來這裡。\n或是打開書庫，讀之前聽過的那幾集。"
        case .failed(let message):
            return message
        }
    }
}
