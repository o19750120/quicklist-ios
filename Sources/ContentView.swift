import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: TodoStore
    @State private var draft = ""
    @FocusState private var inputFocused: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                inputBar
                listSection
            }
            .navigationTitle("QuickList")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("清除已完成", systemImage: "trash", role: .destructive) {
                            withAnimation { store.clearCompleted() }
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .safeAreaInset(edge: .bottom) {
                Text("還有 \(store.remainingCount) 件事沒做")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(.bar)
            }
        }
    }

    private var inputBar: some View {
        HStack(spacing: 12) {
            TextField("要做什麼？", text: $draft)
                .textFieldStyle(.roundedBorder)
                .focused($inputFocused)
                .onSubmit(submit)

            Button(action: submit) {
                Image(systemName: "plus.circle.fill")
                    .font(.title2)
            }
            .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(.horizontal)
        .padding(.vertical, 12)
    }

    private var listSection: some View {
        List {
            ForEach(store.items) { item in
                TodoRow(item: item) {
                    withAnimation { store.toggle(item) }
                }
            }
            .onDelete { store.remove(at: $0) }
            .onMove { store.move(from: $0, to: $1) }
        }
        .listStyle(.insetGrouped)
        .overlay {
            if store.items.isEmpty {
                ContentUnavailableFallback()
            }
        }
    }

    private func submit() {
        withAnimation { store.add(draft) }
        draft = ""
        inputFocused = true
    }
}

private struct TodoRow: View {
    let item: TodoItem
    let onToggle: () -> Void

    var body: some View {
        Button(action: onToggle) {
            HStack(spacing: 12) {
                Image(systemName: item.isDone ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(item.isDone ? Color.accentColor : Color.secondary)
                    .font(.title3)

                Text(item.title)
                    .strikethrough(item.isDone, color: .secondary)
                    .foregroundStyle(item.isDone ? Color.secondary : Color.primary)

                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

private struct ContentUnavailableFallback: View {
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "checklist")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
            Text("清單是空的")
                .font(.headline)
            Text("在上面輸入一件事，按 + 加進來")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .padding()
    }
}

#Preview {
    ContentView().environmentObject(TodoStore())
}
