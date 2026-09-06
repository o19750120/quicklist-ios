import SwiftUI

/// 浮在逐字稿右緣的小控制條。
///
/// 放在側邊而不是工具列，是因為讀逐字稿時手就在畫面上，
/// 切換不必把視線拉回頂端；工具列也已經擠了。
///
/// 目前只管一件事：點一句話要做什麼。點句子跳轉與點詞查意思
/// 搶同一個手勢，而日文的詞佔滿整行、沒有真正的「空白處」可以分流，
/// 所以改成明講的模式切換。
struct SideAssistant: View {

    @Binding var interaction: TranscriptModel.Interaction
    /// 這一集沒有詞表就不顯示查詞那顆，但重聽照樣可用
    let canLookUpWords: Bool
    let repeatsCurrentLine: Bool
    let onToggleRepeat: () -> Void

    @State private var showsHint = false
    @State private var hint = ""

    var body: some View {
        VStack(spacing: Theme.Space.xs) {
            if canLookUpWords {
                ForEach(TranscriptModel.Interaction.allCases) { mode in
                    button(for: mode)
                }

                Divider()
                    .frame(width: 24)
                    .overlay(Theme.textSecondary.opacity(0.3))
            }

            repeatButton
        }
        .padding(Theme.Space.xs)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.lg, style: .continuous)
                .fill(.ultraThinMaterial)
        )
        .overlay(alignment: .leading) { hintBubble }
        .padding(.trailing, Theme.Space.md)
    }

    /// 播完一句要不要跳回句首重來。跟上面兩顆是不同維度的事，
    /// 所以用分隔線隔開，而且是開關不是三選一。
    private var repeatButton: some View {
        Button {
            onToggleRepeat()
            flash(repeatsCurrentLine ? "播完整段往下走" : "這一句播完會跳回句首")
        } label: {
            Image(systemName: "repeat.1")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(repeatsCurrentLine ? Color.black : Theme.textSecondary)
                .frame(width: 44, height: 44)
                .background(
                    Circle().fill(repeatsCurrentLine ? Theme.accent : Color.clear)
                )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("逐句重聽")
        .accessibilityIdentifier("assistant.repeat")
    }

    private func button(for mode: TranscriptModel.Interaction) -> some View {
        let isOn = interaction == mode

        return Button {
            guard !isOn else { return }
            interaction = mode
            flash(mode.hint)
        } label: {
            Image(systemName: mode.icon)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(isOn ? Color.black : Theme.textSecondary)
                .frame(width: 44, height: 44)
                .background(
                    Circle().fill(isOn ? Theme.accent : Color.clear)
                )
        }
        .buttonStyle(.plain)
        .accessibilityLabel(mode.label)
        .accessibilityIdentifier("assistant.mode.\(mode.rawValue)")
    }

    /// 切換之後短暫說明現在點下去會發生什麼 —— 模式本身看圖示猜不出來。
    @ViewBuilder
    private var hintBubble: some View {
        if showsHint {
            Text(hint)
                .font(.caption)
                .foregroundStyle(Theme.textPrimary)
                .padding(.horizontal, Theme.Space.md)
                .padding(.vertical, Theme.Space.sm)
                .background(
                    Capsule().fill(.ultraThinMaterial)
                )
                .fixedSize()
                .offset(x: -Theme.Space.sm)
                .transition(.opacity)
                .allowsHitTesting(false)
        }
    }

    private func flash(_ message: String) {
        hint = message
        withAnimation(.easeOut(duration: 0.15)) { showsHint = true }
        Task {
            try? await Task.sleep(nanoseconds: 1_600_000_000)
            withAnimation(.easeIn(duration: 0.3)) { showsHint = false }
        }
    }
}
