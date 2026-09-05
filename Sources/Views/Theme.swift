import SwiftUI

/// 全 App 共用的配色。集中在一處，之後要換風格只改這裡。
enum Theme {
    static let background = Color(red: 0.055, green: 0.067, blue: 0.086)
    static let surface = Color(red: 0.090, green: 0.106, blue: 0.133)
    static let surfaceRaised = Color(red: 0.129, green: 0.149, blue: 0.184)

    static let textPrimary = Color(red: 0.949, green: 0.957, blue: 0.969)
    static let textSecondary = Color(red: 0.545, green: 0.584, blue: 0.647)

    static let accent = Color(red: 0.910, green: 0.475, blue: 0.290)
    static let spotifyGreen = Color(red: 0.114, green: 0.725, blue: 0.329)

    static let backdrop = LinearGradient(
        colors: [
            Color(red: 0.078, green: 0.094, blue: 0.125),
            Color(red: 0.043, green: 0.051, blue: 0.067)
        ],
        startPoint: .top,
        endPoint: .bottom
    )
}

extension View {
    /// 深色底 + 隱藏系統背景，讓每個畫面長得一致
    func kikitoriBackground() -> some View {
        self
            .background(Theme.backdrop.ignoresSafeArea())
            .preferredColorScheme(.dark)
    }
}
