import SwiftUI

/// 還沒連上 Spotify 時的畫面。
struct LoginView: View {
    @EnvironmentObject private var auth: SpotifyAuth
    @State private var isWorking = false
    @State private var showClientIDField = false

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: Theme.Space.xl) {
                Image(systemName: "waveform.and.mic")
                    .font(.system(size: Theme.heroIcon))
                    .foregroundStyle(Theme.accent)

                VStack(spacing: Theme.Space.sm) {
                    Text("Kikitori")
                        .font(.largeTitle.weight(.bold))
                        .foregroundStyle(Theme.textPrimary)
                    Text("聽 podcast 學語言")
                        .font(.subheadline)
                        .foregroundStyle(Theme.textSecondary)
                }
            }

            Spacer()

            VStack(spacing: Theme.Space.lg) {
                if !auth.hasClientID || showClientIDField {
                    clientIDField
                }

                Button {
                    Task {
                        isWorking = true
                        await auth.signIn()
                        isWorking = false
                    }
                } label: {
                    HStack(spacing: Theme.Space.md) {
                        if isWorking {
                            ProgressView().tint(.black)
                        } else {
                            Image(systemName: "link")
                        }
                        Text(isWorking ? "連線中…" : "連接 Spotify")
                            .font(.body.weight(.semibold))
                    }
                    .foregroundStyle(.black)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, Theme.Space.lg)
                    .background(Theme.spotifyGreen, in: Capsule())
                }
                .disabled(isWorking || !auth.hasClientID)
                .opacity(auth.hasClientID ? 1 : 0.4)

                if let error = auth.lastError {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(Theme.accent)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, Theme.Space.sm)
                }

                if auth.hasClientID && !showClientIDField {
                    Button("換一組 Client ID") {
                        showClientIDField = true
                    }
                    .font(.footnote)
                    .tint(Theme.textSecondary)
                }

                Text("會開啟 Spotify 的授權頁面，登入後自動跳回這裡。\n只讀取你正在播什麼，不會動你的播放清單。")
                    .font(.caption)
                    .foregroundStyle(Theme.textSecondary)
                    .multilineTextAlignment(.center)
                    .padding(.top, Theme.Space.xs)
            }
            .padding(.horizontal, Theme.Space.xxl)
            .padding(.bottom, Theme.Space.xxl)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .kikitoriBackground()
    }

    private var clientIDField: some View {
        VStack(alignment: .leading, spacing: Theme.Space.sm) {
            Text("Spotify Client ID")
                .font(.caption)
                .foregroundStyle(Theme.textSecondary)

            TextField("貼上 Client ID", text: $auth.clientID)
                .textFieldStyle(.plain)
                .font(.system(.footnote, design: .monospaced))
                .foregroundStyle(Theme.textPrimary)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .padding(Theme.Space.lg)
                .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.Radius.md))
        }
    }
}
