import Foundation

struct TodoItem: Identifiable, Codable, Equatable {
    var id = UUID()
    var title: String
    var isDone: Bool = false
    var createdAt: Date = Date()
}

/// 待辦清單的資料來源，用 UserDefaults 做本機持久化。
final class TodoStore: ObservableObject {
    @Published private(set) var items: [TodoItem] = [] {
        didSet { save() }
    }

    private let storageKey = "quicklist.items"

    init() {
        load()
    }

    var remainingCount: Int {
        items.filter { !$0.isDone }.count
    }

    func add(_ title: String) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        items.insert(TodoItem(title: trimmed), at: 0)
    }

    func toggle(_ item: TodoItem) {
        guard let index = items.firstIndex(where: { $0.id == item.id }) else { return }
        items[index].isDone.toggle()
    }

    func remove(at offsets: IndexSet) {
        items.remove(atOffsets: offsets)
    }

    func move(from source: IndexSet, to destination: Int) {
        items.move(fromOffsets: source, toOffset: destination)
    }

    func clearCompleted() {
        items.removeAll { $0.isDone }
    }

    // MARK: - 持久化

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let decoded = try? JSONDecoder().decode([TodoItem].self, from: data) else {
            items = [
                TodoItem(title: "在 iPad 上打開這個 App"),
                TodoItem(title: "改一行 SwiftUI 程式碼"),
                TodoItem(title: "push 到 GitHub 看它自動出新版")
            ]
            return
        }
        items = decoded
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(items) else { return }
        UserDefaults.standard.set(data, forKey: storageKey)
    }
}
