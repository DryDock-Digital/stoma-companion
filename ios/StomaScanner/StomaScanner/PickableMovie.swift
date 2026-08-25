import CoreTransferable
import Foundation
import UniformTypeIdentifiers

struct PickableMovie: Transferable {
    let url: URL

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { movie in
            SentTransferredFile(movie.url)
        } importing: { received in
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent("picked-video-\(UUID().uuidString).mov", isDirectory: false)
            try FileManager.default.copyItem(at: received.file, to: dest)
            return PickableMovie(url: dest)
        }
    }
}
