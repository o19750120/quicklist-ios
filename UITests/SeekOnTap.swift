import XCTest

/// 驗證「點某一句，Spotify 就跳到那裡」真的會動。
///
/// 這個測試只負責點下去並回報點了第幾句；Spotify 那頭跳到哪，
/// 是在 Mac 上用 AppleScript 對照的（模擬器裡看不到 Spotify）。
///
///     xcodebuild test -only-testing:KikitoriUITests/SeekOnTap ...
///
/// 前提：Spotify 正在播一集有逐字稿的節目，而且帳號是 Premium
/// （跳轉走 Web API 的播放控制，免費帳號會拿到 403）。
final class SeekOnTap: XCTestCase {

    func testTapLineSeeksSpotify() {
        let app = XCUIApplication()
        app.launch()

        // LazyVStack 只建可見範圍的 view，所以不能指定一個固定的句號 ——
        // 那一句可能根本還沒被建立。改成點畫面上實際存在的最後一句，
        // 它離目前播放位置夠遠，跳轉才看得出來。
        let lines = app.descendants(matching: .any)
            .matching(NSPredicate(format: "identifier BEGINSWITH 'transcript.line.'"))

        guard lines.firstMatch.waitForExistence(timeout: 20) else {
            XCTFail("等不到逐字稿，Spotify 可能沒在播有逐字稿的那一集")
            return
        }

        // count 會把還沒真的鋪出來的也算進去，取那個索引會抓不到 ——
        // 只從真的可以點的元素裡挑，取最下面那一句，離目前位置最遠。
        let hittable = lines.allElementsBoundByAccessibilityElement.filter { $0.isHittable }
        guard let target = hittable.last else {
            XCTFail("逐字稿有了，但沒有任何一句是可以點的")
            return
        }
        let identifier = target.identifier
        target.tap()

        // 給 seek 跟接下來那次輪詢一點時間
        sleep(6)

        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "點了 \(identifier) 之後"
        shot.lifetime = .keepAlways
        add(shot)

        // 讓外面的腳本知道點的是哪一句
        print("TAPPED_LINE_IDENTIFIER=\(identifier)")
    }
}
