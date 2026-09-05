import Foundation

/// 建置時由 CI 從 GitHub Secret 覆寫。
/// 版控裡永遠是空的，金鑰不會出現在這個公開 repo。
/// 本機值為空時，App 會要求在設定畫面手動填入。
enum BuildSecrets {
    static let spotifyClientID = ""
}
