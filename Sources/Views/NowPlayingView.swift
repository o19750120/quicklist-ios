import SwiftUI

/// 顯示 Spotify 目前正在播什麼，並讓進度即時跟著跑。
/// 這是整條連動的驗證畫面：這裡會動，代表逐字稿同步的基礎成立。
struct NowPlayingView: View {
    @EnvironmentObject private var auth: SpotifyAuth
    @ObservedObject var model: NowPlayingModel
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 28) {
                    if let playing = model.nowPlaying {
                        artwork(for: playing)
                        titleBlock(for: playing)
                        progressBlock(for: playing)
                        transcriptPlaceholder(for: playing)
                    } else {
                        idleState
                    }
                }
                .padding(.horizontal, 24)
                .padding(.top, 12)
                .padding(.bottom, 40)
            }
            .navigationTitle("Kikitori")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                    .tint(Theme.textSecondary)
                }
            }
            .kikitoriBackground()
            .scrollContentBackground(.hidden)
        }
        .sheet(isPresented: $showSettings) {
            SettingsView(model: model)
                .environmentObject(auth)
        }
        .task {
            model.start()
        }
        .onDisappear {
            model.stop()
        }
    }

    // MARK: - 封面

    private func artwork(for playing: NowPlaying) -> some View {
        AsyncImage(url: playing.artworkURL) { phase in
            switch phase {
            case .success(let image):
                image
                    .resizable()
                    .aspectRatio(1, contentMode: .fit)
            default:
                RoundedRectangle(cornerRadius: 20)
                    .fill(Theme.surfaceRaised)
                    .aspectRatio(1, contentMode: .fit)
                    .overlay {
                        Image(systemName: playing.kind == .episode ? "mic.fill" : "music.note")
                            .font(.system(size: 44))
                            .foregroundStyle(Theme.textSecondary)
                    }
            }
        }
        .frame(maxWidth: 320)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .shadow(color: .black.opacity(0.5), radius: 24, y: 12)
    }

    // MARK: - 標題

    private func titleBlock(for playing: NowPlaying) -> some View {
        VStack(spacing: 8) {
            HStack(spacing: 6) {
                Circle()
                    .fill(playing.isPlaying ? Theme.spotifyGreen : Theme.textSecondary)
                    .frame(width: 7, height: 7)
                Text(playing.isPlaying ? "播放中" : "已暫停")
                    .font(.caption)
                    .foregroundStyle(Theme.textSecondary)
                if playing.kind == .episode {
                    Text("· Podcast")
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                }
            }

            Text(playing.title)
                .font(.title3.weight(.semibold))
                .foregroundStyle(Theme.textPrimary)
                .multilineTextAlignment(.center)
                .lineLimit(3)

            if !playing.subtitle.isEmpty {
                Text(playing.subtitle)
                    .font(.subheadline)
                    .foregroundStyle(Theme.textSecondary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
            }
        }
    }

    // MARK: - 進度

    private func progressBlock(for playing: NowPlaying) -> some View {
        VStack(spacing: 10) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Theme.surfaceRaised)
                    Capsule()
                        .fill(Theme.accent)
                        .frame(width: geo.size.width * progressFraction(for: playing))
                }
            }
            .frame(height: 5)

            HStack {
                Text(model.displayProgressMs.asPlaybackTime)
                Spacer()
                Text(playing.durationMs.asPlaybackTime)
            }
            .font(.caption.monospacedDigit())
            .foregroundStyle(Theme.textSecondary)
        }
    }

    private func progressFraction(for playing: NowPlaying) -> Double {
        guard playing.durationMs > 0 else { return 0 }
        return min(1, max(0, Double(model.displayProgressMs) / Double(playing.durationMs)))
    }

    // MARK: - 逐字稿（下一階段）

    private func transcriptPlaceholder(for playing: NowPlaying) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("逐字稿", systemImage: "text.alignleft")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.textPrimary)

            if playing.kind == .episode {
                Text("下一步會在這裡逐句顯示日文與翻譯，並跟著上面的進度走。")
                    .font(.footnote)
                    .foregroundStyle(Theme.textSecondary)
            } else {
                Text("現在播的是歌曲。這支 App 是為 podcast 做的，去 Spotify 播一集節目再回來。")
                    .font(.footnote)
                    .foregroundStyle(Theme.textSecondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    // MARK: - 沒在播的狀態

    private var idleState: some View {
        VStack(spacing: 16) {
            Image(systemName: "waveform")
                .font(.system(size: 48))
                .foregroundStyle(Theme.textSecondary)

            Text(model.statusMessage ?? "正在跟 Spotify 對狀態…")
                .font(.subheadline)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)

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
        .padding(.top, 80)
    }
}
