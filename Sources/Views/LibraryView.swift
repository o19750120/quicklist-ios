import SwiftUI

/// 書庫：聽過的節目與集數，像書櫃一樣依節目排好。
///
/// 這裡不需要 Spotify 正在播 —— 點進去就能純閱讀，
/// 所以通勤時聽、回家後複習，是兩件可以分開的事。
struct LibraryView: View {
    @ObservedObject private var store = LibraryStore.shared
    @Environment(\.dismiss) private var dismiss
    @State private var confirmClear = false

    var body: some View {
        NavigationStack {
            Group {
                if store.entries.isEmpty {
                    emptyState
                } else {
                    shelf
                }
            }
            .navigationTitle("書庫")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if !store.entries.isEmpty {
                        Menu {
                            Button("清空書庫", role: .destructive) {
                                confirmClear = true
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                        }
                        .tint(Theme.textSecondary)
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
            .kikitoriBackground()
            .confirmationDialog("清空書庫？", isPresented: $confirmClear, titleVisibility: .visible) {
                Button("清空", role: .destructive) { store.removeAll() }
                Button("取消", role: .cancel) {}
            } message: {
                Text("只會刪掉本機的收聽紀錄，逐字稿還在後端，不會消失。")
            }
        }
    }

    // MARK: - 書櫃

    private var shelf: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 26) {
                ForEach(store.groupedByShow, id: \.show) { group in
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 8) {
                            Text(group.show)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(Theme.textPrimary)
                            Text("\(group.entries.count) 集")
                                .font(.caption)
                                .foregroundStyle(Theme.textSecondary)
                        }

                        VStack(spacing: 8) {
                            ForEach(group.entries) { entry in
                                NavigationLink {
                                    ReaderView(entry: entry)
                                } label: {
                                    row(entry)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 16)
        }
    }

    private func row(_ entry: LibraryEntry) -> some View {
        HStack(spacing: 12) {
            AsyncImage(url: entry.artworkImageURL) { phase in
                if case .success(let image) = phase {
                    image.resizable().aspectRatio(contentMode: .fill)
                } else {
                    Theme.surfaceRaised
                }
            }
            .frame(width: 52, height: 52)
            .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))

            VStack(alignment: .leading, spacing: 6) {
                Text(entry.episodeTitle)
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(Theme.textPrimary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                HStack(spacing: 6) {
                    if entry.hasTranscript {
                        Label("\(entry.lineCount) 句", systemImage: "text.alignleft")
                    } else {
                        Label("沒有逐字稿", systemImage: "text.badge.xmark")
                    }
                    Text("·")
                    Text(progressLabel(entry))
                }
                .font(.caption2)
                .foregroundStyle(entry.hasTranscript ? Theme.textSecondary : Theme.textSecondary.opacity(0.6))
                .labelStyle(.titleAndIcon)

                progressBar(entry)
            }

            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Theme.textSecondary.opacity(0.5))
        }
        .padding(12)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .contextMenu {
            Button(role: .destructive) {
                LibraryStore.shared.remove(episodeID: entry.episodeID)
            } label: {
                Label("從書庫移除", systemImage: "trash")
            }
        }
    }

    private func progressBar(_ entry: LibraryEntry) -> some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.surfaceRaised)
                Capsule()
                    .fill(entry.isFinished ? Theme.spotifyGreen : Theme.accent)
                    .frame(width: max(2, geo.size.width * entry.completion))
            }
        }
        .frame(height: 3)
    }

    private func progressLabel(_ entry: LibraryEntry) -> String {
        if entry.isFinished { return "已聽完" }
        if entry.lastPositionMs < 1000 { return "還沒開始" }
        return "聽到 \(entry.lastPositionMs.asPlaybackTime) · \(Int(entry.completion * 100))%"
    }

    // MARK: - 空的時候

    private var emptyState: some View {
        VStack(spacing: 14) {
            Image(systemName: "books.vertical")
                .font(.system(size: 42))
                .foregroundStyle(Theme.textSecondary)
            Text("書庫還是空的")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.textPrimary)
            Text("在 Spotify 播一集 podcast，聽過的就會自動收進這裡。")
                .font(.caption)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
        }
        .padding(.horizontal, 40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
