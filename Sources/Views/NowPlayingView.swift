import SwiftUI

/// 主畫面：上方顯示 Spotify 正在播什麼，下方是跟著跑的逐字稿。
struct NowPlayingView: View {
    @EnvironmentObject private var auth: SpotifyAuth
    @ObservedObject var model: NowPlayingModel
    @StateObject private var transcriptModel = TranscriptModel()

    @State private var showSettings = false
    @State private var showDiagnostics = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if let playing = model.nowPlaying {
                    header(playing)

                    Rectangle()
                        .fill(Theme.surfaceRaised)
                        .frame(height: 1)

                    if playing.kind == .episode {
                        TranscriptView(
                            transcriptModel: transcriptModel,
                            nowPlayingModel: model,
                            episode: playing
                        )
                    } else {
                        notAPodcast
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
            Task { await transcriptModel.loadIfNeeded(episodeID: newID) }
        }
        .onChange(of: model.displayProgressMs) { _ in
            transcriptModel.updatePosition(model.alignedProgressMs)
        }
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
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

    // MARK: - 上方資訊列

    private func header(_ playing: NowPlaying) -> some View {
        VStack(spacing: 12) {
            HStack(spacing: 14) {
                AsyncImage(url: playing.artworkURL) { phase in
                    if case .success(let image) = phase {
                        image.resizable().aspectRatio(contentMode: .fill)
                    } else {
                        Theme.surfaceRaised
                    }
                }
                .frame(width: 56, height: 56)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

                VStack(alignment: .leading, spacing: 3) {
                    Text(playing.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.textPrimary)
                        .lineLimit(2)

                    HStack(spacing: 5) {
                        Circle()
                            .fill(playing.isPlaying ? Theme.spotifyGreen : Theme.textSecondary)
                            .frame(width: 6, height: 6)
                        Text(playing.subtitle)
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)
                            .lineLimit(1)
                    }
                }

                Spacer(minLength: 0)
            }

            VStack(spacing: 6) {
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
        .padding(.horizontal, 20)
        .padding(.top, 8)
        .padding(.bottom, 14)
    }

    private var offsetLabel: String {
        String(format: "校正 %+.1fs", Double(model.alignmentOffsetMs) / 1000)
    }

    private func progressFraction(_ playing: NowPlaying) -> Double {
        guard playing.durationMs > 0 else { return 0 }
        return min(1, max(0, Double(model.displayProgressMs) / Double(playing.durationMs)))
    }

    // MARK: - 其他狀態

    private var notAPodcast: some View {
        VStack(spacing: 14) {
            Image(systemName: "music.note")
                .font(.system(size: 38))
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
        VStack(spacing: 16) {
            Image(systemName: "waveform")
                .font(.system(size: 48))
                .foregroundStyle(Theme.textSecondary)

            Text(model.statusMessage ?? "正在跟 Spotify 對狀態…")
                .font(.subheadline)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)

            Button {
                Task { await model.refresh() }
            } label: {
                Text("重新整理")
                    .font(.subheadline.weight(.medium))
                    .padding(.horizontal, 20)
                    .padding(.vertical, 10)
                    .background(Theme.surfaceRaised, in: Capsule())
            }
            .tint(Theme.textPrimary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
