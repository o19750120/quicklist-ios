import SwiftUI

@main
struct KikitoriApp: App {
    @StateObject private var auth = SpotifyAuth()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(auth)
        }
    }
}

/// 依照有沒有連上 Spotify 決定顯示哪個畫面。
struct RootView: View {
    @EnvironmentObject private var auth: SpotifyAuth
    @State private var model: NowPlayingModel?

    var body: some View {
        Group {
            if auth.isAuthorized, let model {
                NowPlayingView(model: model)
            } else {
                LoginView()
            }
        }
        .onAppear { syncModel() }
        .onChange(of: auth.isAuthorized) { _ in syncModel() }
    }

    /// NowPlayingModel 需要 auth，而 auth 來自 environment，
    /// 所以不能在 init 建，改成第一次需要時再建。
    private func syncModel() {
        if auth.isAuthorized, model == nil {
            model = NowPlayingModel(auth: auth)
        } else if !auth.isAuthorized {
            model?.stop()
            model = nil
        }
    }
}
