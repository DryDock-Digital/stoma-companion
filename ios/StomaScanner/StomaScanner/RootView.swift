import SwiftUI

struct RootView: View {
    @State private var showCapture = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("StomaScanner")
                        .font(.largeTitle.bold())

                    Text(
                        "Build a 3D model from photos or video using RealityKit Object Capture on supported hardware. "
                            + "Use Stoma Companion on Mac for off-device processing and base perimeter export."
                    )
                    .foregroundStyle(.secondary)

                    Button("Object capture") {
                        showCapture = true
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                }
                .padding(24)
            }
            .navigationBarTitleDisplayMode(.inline)
        }
        .fullScreenCover(isPresented: $showCapture) {
            PhotogrammetryCaptureView()
        }
    }
}
