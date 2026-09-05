import SwiftUI

/// 全 App 共用的配色。集中在一處，之後要換風格只改這裡。
enum Theme {
    static let background = Color(red: 0.055, green: 0.067, blue: 0.086)
    static let surface = Color(red: 0.090, green: 0.106, blue: 0.133)
    static let surfaceRaised = Color(red: 0.129, green: 0.149, blue: 0.184)

    static let textPrimary = Color(red: 0.949, green: 0.957, blue: 0.969)
    /// 次要文字。在這個深色底上它的對比是 6.2:1。
    ///
    /// 不要再用 `.opacity()` 從它疊出「第三層」文字色 ——
    /// 算過了，0.7 只剩 3.6:1、0.6 只剩 3.0:1，都低於 AA 要求的 4.5:1，
    /// 而它本身已經幾乎踩在那條線上，再暗一點就不合格。
    /// 要做層級請用字級或字重，不要用透明度。
    static let textSecondary = Color(red: 0.545, green: 0.584, blue: 0.647)

    static let accent = Color(red: 0.910, green: 0.475, blue: 0.290)
    static let spotifyGreen = Color(red: 0.114, green: 0.725, blue: 0.329)

    /// 間距一律取自這裡，全部是 4 的倍數。
    ///
    /// 之前散落著 5、6、9、10、14、18、26 這些數字，同一種關係在不同畫面
    /// 用不同的值 —— 那是「看起來有點不對但說不上來」的主因。
    /// 對齊到同一組刻度之後，畫面的節奏才會一致。
    enum Space {
        /// 4 —— 貼在一起的東西（標籤與它的說明）
        static let xs: CGFloat = 4
        /// 8 —— 同一組內的間隔
        static let sm: CGFloat = 8
        /// 12 —— 卡片內距、列與列之間
        static let md: CGFloat = 12
        /// 16 —— 區塊內距
        static let lg: CGFloat = 16
        /// 24 —— 區塊之間
        static let xl: CGFloat = 24
        /// 32 —— 大留白（空狀態、置中內容的左右）
        static let xxl: CGFloat = 32
    }

    /// 圓角。跟間距一樣只留三級，卡片與按鈕才不會各自為政。
    enum Radius {
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
    }

    /// 空狀態那個大圖示。以前 34/38/42/48/56 五種大小在做同一件事。
    static let heroIcon: CGFloat = 44

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
