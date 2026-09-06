import XCTest

/// 開啟逐句重聽之後，播到句尾應該跳回句首。
///
/// 這裡只負責按下開關再等一段時間；Spotify 的位置有沒有倒退，
/// 是在 Mac 那頭用 AppleScript 取樣對照的。
///
/// 注意：只能用 identifier 指名元素，不要搜尋或遍歷 ——
/// 逐字稿畫面上有七百多個 accessibility 元素，遍歷會超時。
final class RepeatLine: XCTestCase {

    func testRepeatKeepsPlayheadInOneLine() {
        let app = XCUIApplication()
        app.launch()

        let toggle = app.buttons["assistant.repeat"]
        guard toggle.waitForExistence(timeout: 30) else {
            XCTFail("找不到逐句重聽的開關 —— 逐字稿可能還沒載入")
            return
        }
        toggle.tap()

        // 留時間讓它至少跨過一次句尾
        sleep(30)

        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "逐句重聽開啟 30 秒後"
        shot.lifetime = .keepAlways
        add(shot)
    }
}
