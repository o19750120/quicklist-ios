import SwiftUI

/// 點一個詞之後跳出來的解釋。
///
/// 內容全部來自逐字稿裡附帶的詞表，查詞不連網。
/// 查不到的詞（多半是數字與英文縮寫，實測約 3.4%）給外部辭典的連結 ——
/// 開連結沒有授權問題，抓資料才有。
struct WordDetailView: View {

    let word: String
    let reading: String?
    /// 後端兩個模型對這個詞的讀法意見不同，該讓使用者看到候選而不是默默挑一個
    let isAmbiguous: Bool
    let entry: Vocabulary.Entry?

    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.Space.xl) {
                    heading

                    if let entry, entry.hasChinese {
                        senses("中文", entry.chinese, tint: Theme.textPrimary)
                    }
                    if let entry, !entry.english.isEmpty {
                        senses("English", entry.english, tint: Theme.textSecondary)
                    }
                    if entry == nil {
                        notFound
                    }

                    externalLinks
                }
                .padding(Theme.Space.xl)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
            .kikitoriBackground()
        }
    }

    // MARK: - 詞本身

    private var heading: some View {
        VStack(alignment: .leading, spacing: Theme.Space.sm) {
            Text(word)
                .font(.largeTitle.weight(.semibold))
                .foregroundStyle(Theme.textPrimary)

            HStack(spacing: Theme.Space.sm) {
                if let reading, !reading.isEmpty, reading != word {
                    Text(reading)
                        .font(.headline)
                        .foregroundStyle(Theme.accent)
                }
                if let entry, !entry.partOfSpeech.isEmpty {
                    Text(entry.partOfSpeech)
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .padding(.horizontal, Theme.Space.sm)
                        .padding(.vertical, 2)
                        .background(Theme.surface, in: Capsule())
                }
            }

            if isAmbiguous, let alternates = entry?.alternateReadings, !alternates.isEmpty {
                ambiguousReadings(alternates)
            } else if entry?.readingIsGuess == true {
                Label("這個讀音是推測的", systemImage: "questionmark.circle")
                    .font(.caption)
                    .foregroundStyle(Theme.textSecondary)
            }
        }
    }

    /// 只在後端真的判定有歧義時才顯示。
    ///
    /// 不能拿「字典裡有其他讀音」當判斷依據 —— 三分之一的詞都會中，
    /// 那樣整個畫面都是候選，反而變成雜訊。
    private func ambiguousReadings(_ alternates: [String]) -> some View {
        VStack(alignment: .leading, spacing: Theme.Space.xs) {
            Label("這裡有兩種讀法", systemImage: "exclamationmark.triangle")
                .font(.caption.weight(.medium))
                .foregroundStyle(Theme.accent)
            Text(alternates.joined(separator: "　／　"))
                .font(.subheadline)
                .foregroundStyle(Theme.textPrimary)
        }
        .padding(Theme.Space.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.Radius.md,
                                                        style: .continuous))
    }

    // MARK: - 釋義

    private func senses(_ title: String, _ items: [String], tint: Color) -> some View {
        VStack(alignment: .leading, spacing: Theme.Space.sm) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.textSecondary)

            VStack(alignment: .leading, spacing: Theme.Space.sm) {
                ForEach(Array(items.prefix(8).enumerated()), id: \.offset) { index, sense in
                    HStack(alignment: .firstTextBaseline, spacing: Theme.Space.sm) {
                        Text("\(index + 1)")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(Theme.textSecondary)
                            .frame(width: 16, alignment: .trailing)
                        Text(sense)
                            .font(.subheadline)
                            .foregroundStyle(tint)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
    }

    private var notFound: some View {
        VStack(alignment: .leading, spacing: Theme.Space.sm) {
            Text("詞表裡沒有這個詞")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.textPrimary)
            Text("詞表是每一集各自建的，涵蓋約 96%，沒收到的多半是數字或英文縮寫。")
                .font(.caption)
                .foregroundStyle(Theme.textSecondary)
        }
    }

    // MARK: - 外部辭典

    private var externalLinks: some View {
        VStack(alignment: .leading, spacing: Theme.Space.sm) {
            Text("查更多")
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.textSecondary)

            HStack(spacing: Theme.Space.sm) {
                externalLink("Weblio", "https://www.weblio.jp/content/")
                externalLink("Jisho", "https://jisho.org/search/")
                externalLink("日中", "https://cjjc.weblio.jp/content/")
            }
        }
    }

    private func externalLink(_ title: String, _ prefix: String) -> some View {
        Button {
            let encoded = word.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? word
            if let url = URL(string: prefix + encoded) {
                openURL(url)
            }
        } label: {
            Text(title)
                .font(.footnote.weight(.medium))
                .foregroundStyle(Theme.textPrimary)
                .padding(.horizontal, Theme.Space.lg)
                .padding(.vertical, Theme.Space.sm)
                .background(Theme.surface, in: Capsule())
        }
    }
}
