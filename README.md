# Stoma Companion — collaborator handoff

macOS app for USDZ intake, base-perimeter extraction, ArUco/photo scale, and G-code / polar export.

## Requirements

- macOS 14+
- Xcode 15+ (Apple Silicon recommended for Object Capture)
- Open **StomaCompanion** scheme (not StomaScanner)

## Open & run

1. Unzip this archive.
2. Open `ios/StomaScanner/StomaScanner.xcodeproj` in Xcode.
3. Select the **StomaCompanion** scheme and **My Mac**.
4. Build and run (⌘R).

You may need to set your own **Development Team** under Signing & Capabilities.

## Layout

```
StomaCompanion-handoff/
├── README.md
├── Stoma Companion Overview.pdf
└── ios/StomaScanner/
    ├── CompanionMac/
    ├── SharedPhotogrammetry/
    ├── StomaScanner/
    ├── StomaScanner.xcodeproj
    └── project.yml
```

## Notes

- Primary work is under `CompanionMac/`. Shared reconstruction code is in `SharedPhotogrammetry/`.
- The Xcode project also contains an iOS **StomaScanner** target; collaborators focused on Companion can ignore that scheme.
- Build caches and personal Xcode userdata are omitted.
