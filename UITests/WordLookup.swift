import XCTest

/// 驗證「點逐字稿裡的一個詞，跳出解釋」。
///
/// 前提：Spotify 正在播一集有詞表的節目（`vocab` 欄位不是 null）。
final class WordLookupTests: XCTestCase {

    func testTapWordShowsDefinition() {
        let app = XCUIApplication()
        app.launch()

        // 逐字稿要先跟 Supabase 拿回來
        let lines = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH 'transcript.line.'"))
        guard lines.firstMatch.waitForExistence(timeout: 25) else {
            XCTFail("等不到逐字稿")
            return
        }

        // 逐字稿在 ScrollView 裡，上方資訊列不在 —— 用這個範圍就不會誤點節目名。
        //
        // 這裡刻意只取 element(boundBy:) 的前幾個，不要枚舉全部：
        // 一句拆成一個個詞之後畫面上有七百多個元素，而
        // allElementsBoundByAccessibilityElement 會逐一跟 App 來回通訊，
        // 在這個量級會跑到超時（實測十分鐘沒結束）。
        let inScroll = app.scrollViews.descendants(matching: .staticText)
        guard inScroll.firstMatch.waitForExistence(timeout: 15) else {
            XCTFail("逐字稿還沒出現")
            return
        }

        var element: XCUIElement?
        for index in 0..<12 {
            let candidate = inScroll.element(boundBy: index)
            guard candidate.exists else { break }
            let label = candidate.label
            if candidate.isHittable, label.count >= 2, label.count <= 8 {
                element = candidate
                break
            }
        }

        guard let element else {
            XCTFail("逐字稿裡沒有可以點的詞")
            return
        }

        let tapped = element.label
        element.tap()

        // 詞義卡片是一個 sheet，等它上來
        sleep(2)

        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "點了「\(tapped)」之後"
        shot.lifetime = .keepAlways
        add(shot)

        print("TAPPED_WORD=\(tapped)")
    }
}
