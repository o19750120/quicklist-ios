import Foundation

/// 一集的詞表與詞邊界，轉錄時就由後端建好，跟逐字稿一起下載。
///
/// 日文句子沒有空格，所以「使用者點到哪個詞」這件事 App 自己算不出來 ——
/// `spans` 就是後端算好的答案。查詞完全在本機完成，不打任何 API，
/// 也不需要把 293 MB 的字典塞進 App。
struct Vocabulary: Equatable {

    /// 詞表的一個條目。鍵是詞的原形（lemma）。
    struct Entry: Equatable {
        let reading: String
        let partOfSpeech: String
        /// 繁體中文釋義
        let chinese: [String]
        /// JMdict 的英文釋義
        let english: [String]
        /// 這個詞形在字典裡還有別的讀音。
        ///
        /// 注意：**有 alt 不等於這裡讀錯了**。三分之一的詞都有 alt，
        /// 拿它當「不確定」的依據會讓畫面到處都是候選。
        /// 真正的不確定看 `Span.isAmbiguous`。
        let alternateReadings: [String]
        /// 讀音是推測的，標假名時要保守一點
        let readingIsGuess: Bool

        var hasChinese: Bool { !chinese.isEmpty }
    }

    /// 句子裡的一段。對應原文的 `[start..<end)` 字元範圍。
    struct Span: Equatable {
        let start: Int
        let end: Int
        /// 查字典要用的原形。助詞、標點這類不用查的是 nil，
        /// 但一樣要留著，否則詞的邊界會畫錯。
        let lemma: String?
        /// 後端覆核過的讀音，覆蓋詞表裡的 `reading`
        let overrideReading: String?
        /// 後端兩個模型對這裡的讀法意見不同，該讓使用者看到候選
        let isAmbiguous: Bool

        var isLookupable: Bool { lemma != nil }
    }

    let entries: [String: Entry]
    /// 每一句一組，順序跟 `Transcript.lines` 對齊
    let spans: [[Span]]

    func entry(for lemma: String) -> Entry? { entries[lemma] }

    func spans(forLine index: Int) -> [Span] {
        guard index >= 0, index < spans.count else { return [] }
        return spans[index]
    }

    /// 這一段實際要顯示的讀音：後端覆核過的優先，否則用詞表的。
    func reading(for span: Span) -> String? {
        if let override = span.overrideReading { return override }
        guard let lemma = span.lemma else { return nil }
        return entries[lemma]?.reading
    }

    // MARK: - 解析

    /// 從 Supabase 的 `vocab` 欄位解析。格式不對就回 nil，讓 App 退回沒有查詞功能的狀態。
    init?(json: Any?) {
        guard let root = json as? [String: Any],
              let rawEntries = root["vocab"] as? [String: Any],
              let rawTokens = root["tokens"] as? [[Any]] else {
            return nil
        }

        var entries: [String: Entry] = [:]
        entries.reserveCapacity(rawEntries.count)

        for (lemma, value) in rawEntries {
            guard let item = value as? [String: Any] else { continue }
            entries[lemma] = Entry(
                reading: item["r"] as? String ?? "",
                partOfSpeech: item["p"] as? String ?? "",
                chinese: item["zh"] as? [String] ?? [],
                english: item["en"] as? [String] ?? [],
                alternateReadings: item["alt"] as? [String] ?? [],
                readingIsGuess: item["guess"] as? Bool ?? false
            )
        }

        // 一段是 [起, 迄, lemma?, 第四元素?]。第四元素是 "?" 代表有歧義，
        // 其他字串則是覆核後的讀音。
        let spans: [[Span]] = rawTokens.map { line in
            line.compactMap { raw in
                guard let span = raw as? [Any], span.count >= 2,
                      let start = span[0] as? Int,
                      let end = span[1] as? Int else { return nil }

                let lemma = span.count > 2 ? span[2] as? String : nil
                let fourth = span.count > 3 ? span[3] as? String : nil

                return Span(
                    start: start,
                    end: end,
                    lemma: lemma,
                    overrideReading: fourth == "?" ? nil : fourth,
                    isAmbiguous: fourth == "?"
                )
            }
        }

        guard !entries.isEmpty, !spans.isEmpty else { return nil }
        self.entries = entries
        self.spans = spans
    }
}

/// 使用者點開的那個詞。`lemma` 用來查詞表，`word` 是畫面上實際那幾個字。
struct WordLookup: Identifiable, Equatable {
    let word: String
    let lemma: String
    let reading: String?
    let isAmbiguous: Bool

    var id: String { "\(lemma)-\(word)" }
}
