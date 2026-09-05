import XCTest

/// 自動走過各個畫面並留下截圖。
///
/// 這不是驗收測試，是開發時的「眼睛」—— 在 Mac 上改完版面，
/// 跑一次就能看到每個畫面實際長什麼樣，不必自己一路點過去。
///
///     xcodebuild test -project Kikitori.xcodeproj -scheme Kikitori \
///       -destination 'platform=iOS Simulator,name=iPad Pro 11-inch (M5)' \
///       -only-testing:KikitoriUITests/ScreenTour \
///       -resultBundlePath build/tour.xcresult
///
/// 截圖在 `build/tour.xcresult` 裡，用這行匯出成 png：
///
///     xcrun xcresulttool export attachments \
///       --path build/tour.xcresult --output-path build/screens
final class ScreenTour: XCTestCase {

    private var app: XCUIApplication!

    override func setUp() {
        super.setUp()
        continueAfterFailure = true
        app = XCUIApplication()
        app.launch()
    }

    /// 從主畫面一路走到某一集的閱讀畫面
    func testTour() {
        capture("01-啟動")

        // 沒登入的話就只有登入頁可看，後面的畫面都進不去
        let libraryButton = app.buttons["toolbar.library"]
        guard libraryButton.waitForExistence(timeout: 10) else {
            capture("02-登入頁（沒有連上 Spotify）")
            return
        }

        capture("02-正在播放")

        libraryButton.tap()
        XCTAssertTrue(app.navigationBars["書庫"].waitForExistence(timeout: 5),
                      "點了書庫卻沒有開啟書庫畫面")
        capture("03-書庫")

        // 書櫃裡第一列就是最近聽的那一集。
        // 這裡一定要指名，用 boundBy: 0 會抓到工具列的「完成」把畫面關掉。
        let firstEpisode = app.buttons
            .matching(NSPredicate(format: "identifier BEGINSWITH 'library.row.'"))
            .firstMatch
        if firstEpisode.waitForExistence(timeout: 5) {
            firstEpisode.tap()
            // 逐字稿要跟 Supabase 拿，給它一點時間
            _ = app.staticTexts.firstMatch.waitForExistence(timeout: 10)
            sleep(2)
            capture("04-閱讀模式")
        }
    }

    private func capture(_ name: String) {
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
}
