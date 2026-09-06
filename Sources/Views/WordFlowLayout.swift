import SwiftUI

/// 把一句話的每個詞排成可以換行的一列列。
///
/// 為什麼不能直接用 `Text`：日文句子要能「點某一個詞」，就得讓每個詞
/// 是各自獨立、各自可點的 view，而 `Text` 串接出來的整句只能整句點。
/// 拆成一個個 view 之後就得自己處理換行，這就是這個 Layout 在做的事。
///
/// 按詞換行對日文反而比按字元換行好 —— 一個詞不會被拆到兩行去。
struct WordFlowLayout: Layout {

    /// 詞與詞之間。日文本來就沒有空格，所以是 0，
    /// 詞的邊界靠點擊時的高亮表現，不是靠間隔。
    var horizontalSpacing: CGFloat = 0
    /// 行與行之間
    var lineSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        let rows = arrange(subviews: subviews, maxWidth: maxWidth)

        let height = rows.reduce(CGFloat.zero) { $0 + $1.height } +
            lineSpacing * CGFloat(max(0, rows.count - 1))
        let width = rows.map(\.width).max() ?? 0

        return CGSize(width: min(width, maxWidth), height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
                       subviews: Subviews, cache: inout ()) {
        let rows = arrange(subviews: subviews, maxWidth: bounds.width)
        var y = bounds.minY

        for row in rows {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(
                    at: CGPoint(x: x, y: y + (row.height - size.height) / 2),
                    proposal: ProposedViewSize(size)
                )
                x += size.width + horizontalSpacing
            }
            y += row.height + lineSpacing
        }
    }

    // MARK: - 換行

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(subviews: Subviews, maxWidth: CGFloat) -> [Row] {
        var rows: [Row] = []
        var current = Row()

        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            let needed = current.indices.isEmpty ? size.width
                                                 : current.width + horizontalSpacing + size.width

            if !current.indices.isEmpty && needed > maxWidth {
                rows.append(current)
                current = Row()
                current.indices = [index]
                current.width = size.width
                current.height = size.height
            } else {
                current.indices.append(index)
                current.width = needed
                current.height = max(current.height, size.height)
            }
        }

        if !current.indices.isEmpty { rows.append(current) }
        return rows
    }
}
