import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var auth: SpotifyAuth
    @ObservedObject var model: NowPlayingModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("Spotify") {
                    LabeledContent("狀態") {
                        Text(auth.isAuthorized ? "已連接" : "未連接")
                            .foregroundStyle(auth.isAuthorized ? Theme.spotifyGreen : Theme.textSecondary)
                    }

                    VStack(alignment: .leading, spacing: Theme.Space.sm) {
                        Text("Client ID")
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)
                        TextField("貼上 Client ID", text: $auth.clientID)
                            .font(.system(.footnote, design: .monospaced))
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                    }

                    Button("中斷連接", role: .destructive) {
                        auth.signOut()
                        dismiss()
                    }
                }

                Section {
                    LabeledContent("目前偏移") {
                        Text(offsetDescription)
                            .monospacedDigit()
                            .foregroundStyle(Theme.textSecondary)
                    }
                    Button("清除偏移") {
                        model.resetAlignment()
                    }
                    .disabled(model.alignmentOffsetMs == 0)
                } header: {
                    Text("逐字稿對齊")
                } footer: {
                    Text("Spotify 會在 podcast 插入動態廣告，害逐字稿的時間對不上。之後在逐字稿上點「現在講的是這句」，就會記下偏移量，整份跟著校正。")
                }

                Section("關於") {
                    LabeledContent("版本", value: appVersion)
                }

                // CC BY-SA 4.0 要求標示出處，這是授權義務不是禮貌。
                // ShareAlike 只及於字典「資料」，不及於這個 App 的程式碼。
                Section {
                    VStack(alignment: .leading, spacing: Theme.Space.sm) {
                        Text("JMdict / JMnedict / KANJIDIC")
                            .font(.footnote.weight(.medium))
                            .foregroundStyle(Theme.textPrimary)
                        Text("© Electronic Dictionary Research and Development Group\nCC BY-SA 4.0")
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)

                        Text("繁體中文釋義")
                            .font(.footnote.weight(.medium))
                            .foregroundStyle(Theme.textPrimary)
                            .padding(.top, Theme.Space.xs)
                        Text("© Y1Z (Tomoshi)\nCC BY-SA 4.0")
                            .font(.caption)
                            .foregroundStyle(Theme.textSecondary)
                    }
                    .padding(.vertical, Theme.Space.xs)
                } header: {
                    Text("字典來源")
                } footer: {
                    Text("點單字查到的解釋來自這些開放資料集，全部存在逐字稿裡，查詞不會連網。")
                }
            }
            .navigationTitle("設定")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
            .scrollContentBackground(.hidden)
            .kikitoriBackground()
        }
    }

    private var offsetDescription: String {
        let seconds = Double(model.alignmentOffsetMs) / 1000
        if abs(seconds) < 0.05 { return "無" }
        return String(format: "%+.1f 秒", seconds)
    }

    private var appVersion: String {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "?"
        return "\(version) (\(build))"
    }
}
