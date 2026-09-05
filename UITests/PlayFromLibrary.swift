import XCTest

/// 驗證「書庫點一集，Spotify 就切到那一集並從上次聽到的地方接下去」。
///
/// 跟 SeekOnTap 一樣，這裡只負責點；Spotify 切到哪一集、跳到第幾秒，
/// 是在 Mac 上用 AppleScript 對照的。
final class PlayFromLibrary: XCTestCase {

    func testTapEntryStartsPlayback() {
        let app = XCUIApplication()
        app.launch()

        let library = app.buttons["toolbar.library"]
        guard library.waitForExistence(timeout: 15) else {
            XCTFail("等不到書庫按鈕，可能沒連上 Spotify")
            return
        }
        library.tap()

        let rows = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH 'library.row.'"))
        guard rows.firstMatch.waitForExistence(timeout: 10) else {
            XCTFail("書庫是空的")
            return
        }

        // count 會把還沒鋪出來的也算進去，只從真的可以點的裡面挑。
        // 取最下面那一列 —— 書庫依最近收聽排序，最上面那列多半就是正在播的，
        // 點它看不出有沒有切換。
        let hittable = rows.allElementsBoundByAccessibilityElement.filter { $0.isHittable }
        guard let target = hittable.last else {
            XCTFail("書庫有內容，但沒有一列是可以點的")
            return
        }

        let identifier = target.identifier
        target.tap()

        // 叫 Spotify 換一集、等它回報、書庫收起來，這些都要一點時間
        sleep(8)

        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "從書庫點了 \(identifier)"
        shot.lifetime = .keepAlways
        add(shot)

        print("TAPPED_LIBRARY_ROW=\(identifier)")
    }
}
