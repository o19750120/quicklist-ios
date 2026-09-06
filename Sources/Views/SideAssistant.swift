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
    /// 這一集沒有詞表就沒有查詞可切，整個控制條收起來
    let canLookUpWords: Bool

    @State private var showsHint = false

    var body: some View {
        if canLookUpWords {
            VStack(spacing: Theme.Space.xs) {
                ForEach(TranscriptModel.Interaction.allCases) { mode in
                    button(for: mode)
                }
            }
            .padding(Theme.Space.xs)
            .background(
                RoundedRectangle(cornerRadius: Theme.Radius.lg, style: .continuous)
                    .fill(.ultraThinMaterial)
            )
            .overlay(alignment: .leading) { hint }
            .padding(.trailing, Theme.Space.md)
        }
    }

    private func button(for mode: TranscriptModel.Interaction) -> some View {
        let isOn = interaction == mode

        return Button {
            guard !isOn else { return }
            interaction = mode
            flashHint()
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
    private var hint: some View {
        if showsHint {
            Text(interaction.hint)
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

    private func flashHint() {
        withAnimation(.easeOut(duration: 0.15)) { showsHint = true }
        Task {
            try? await Task.sleep(nanoseconds: 1_600_000_000)
            withAnimation(.easeIn(duration: 0.3)) { showsHint = false }
        }
    }
}
