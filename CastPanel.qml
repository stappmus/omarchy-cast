import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
    id: root
    moduleName: "stappmus.cast"
    ipcTarget: "stappmus.cast"
    manageIpc: false
    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    property string pluginRoot: decodeURIComponent(Qt.resolvedUrl(".").toString().replace(/^file:\/\//, ""))
    property bool busy: false
    property bool ready: false
    property bool exiting: false
    property bool contextMode: false
    property bool failed: false
    property string message: "Discovering Chromecast devices…"
    property var devices: []
    property var audioTracks: [{value: "default", label: "Default audio"}]
    property var subtitleTracks: [{value: "none", label: "Off"}, {value: "external", label: "External subtitle file…"}]
    property var playback: ({connected: false, state: "IDLE", position: 0, duration: 0, volume: 0.5})
    property bool subtitlesEnabled: true
    property int audioDelayMs: 0
    property int progress: -1
    property string phaseName: "idle"
    property string phaseMessage: ""
    property string profileDescription: ""
    property string busyAction: ""
    property int commandSequence: 0
    property string pendingAction: ""
    property int pendingId: 0
    property string pendingState: ""
    property real pendingPosition: -1
    property real pendingVolume: -1
    property var pendingMuted: null
    readonly property bool controlPending: pendingAction !== ""
    readonly property string displayedState: pendingState || playback.state || "IDLE"
    readonly property real displayedPosition: pendingPosition >= 0 ? pendingPosition : (playback.position || 0)
    readonly property real displayedVolume: pendingVolume >= 0 ? pendingVolume : (playback.volume === undefined ? 0.5 : playback.volume)
    readonly property bool displayedMuted: pendingMuted !== null ? pendingMuted : !!playback.muted
    readonly property bool transitioning: preparing || (playback.connected && playback.state === "BUFFERING")
    property string sourcePath: ""
    property real sourceDuration: 0
    property bool optionsExpanded: false
    property bool urlEntry: false
    property bool preparing: false
    property string loadedSubtitle: "none"
    readonly property string videoName: sourcePath ? decodeURIComponent(sourcePath.split("/").pop().split("?")[0]) : "Choose a video"
    readonly property string receiverName: {
        if (host.text.trim()) return host.text.trim()
        for (var i = 0; i < devices.length; i++)
            if (devices[i].value === receiver.value) return devices[i].label.split(" · ")[0]
        return "your TV"
    }

    component Caption: Text {
        color: Color.foreground
        opacity: 0.58
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        textFormat: Text.PlainText
    }
    component Action: Button {
        focusable: true
        opacity: enabled ? 1 : 0.35
        Behavior on opacity { NumberAnimation { duration: 160 } }
    }
    function chooseSource(path) {
        sourcePath = path
        sourceDuration = 0
        urlEntry = false
        inspectSource()
    }

    function send(command) {
        if (!ready) { message = "Cast worker is not running. Exit and reopen Omarchy Cast."; failed = true; return }
        command.id = ++commandSequence
        var controls = ["pause", "seek", "volume", "mute", "subtitles", "stop", "audio_delay"]
        if (controls.indexOf(command.action) >= 0) {
            if (controlPending && command.action !== "stop") return
            pendingAction = command.action
            pendingId = command.id
            if (command.action === "pause") {
                command.paused = playback.state === "PLAYING"
                pendingState = command.paused ? "PAUSED" : "PLAYING"
            } else if (command.action === "seek") {
                pendingPosition = Math.max(0, Math.min(playback.duration || 1e9, Number(command.value) + (command.relative ? (playback.position || 0) : 0)))
            } else if (command.action === "volume") pendingVolume = command.value
            else if (command.action === "mute") { command.muted = !playback.muted; pendingMuted = command.muted }
            controlTimeout.interval = ["seek", "audio_delay"].indexOf(command.action) >= 0 ? 90000 : 12000
            controlTimeout.restart()
        }
        if (command.action === "pick") root.close()
        worker.write(JSON.stringify(command) + "\n")
    }
    function inspectSource() {
        audioTracks = [{value: "default", label: "Default audio"}]
        subtitleTracks = [{value: "none", label: "Off"}, {value: "external", label: "External subtitle file…"}]
        audio.value = "default"; subtitles.value = "none"
        if (root.sourcePath !== "" && !/^https?:\/\//.test(root.sourcePath)) send({action: "inspect", source: root.sourcePath})
    }
    function cast() {
        progress = -1; failed = false; message = "Getting your video ready…"; subtitlesEnabled = true; preparing = true; loadedSubtitle = subtitles.value; optionsExpanded = false
        send({action: "cast", source: root.sourcePath, device: receiver.value, host: host.text,
              subtitle: subtitles.value, external: external.text, language: language.text,
              subtitleSize: subtitleSize.value, audio: audio.value, mode: mode.value,
              quality: quality.value, sound: sound.value, deviceProfile: deviceProfile.value, audioDelay: root.audioDelayMs / 1000, start: Number(start.text) || 0})
    }
    function applyAudioDelay() {
        if (busy || !playback.connected || Math.abs(audioDelayMs / 1000 - (playback.audioDelay || 0)) < 0.001) return
        failed = false
        send({action: "audio_delay", value: audioDelayMs / 1000})
    }
    function exitApp() {
        exiting = true
        close()
        send({action: "exit"})
        exitTimeout.start()
    }
    function disable() { Quickshell.execDetached(["omarchy", "plugin", "disable", "stappmus.cast"]) }
    function clock(seconds) {
        var n = Math.max(0, Math.floor(Number(seconds) || 0))
        return Math.floor(n / 60) + ":" + (n % 60 < 10 ? "0" : "") + n % 60
    }
    function receive(data) {
        var e
        try { e = JSON.parse(data) } catch (_) { return }
        if (e.event === "ready") { ready = true; send({action: "scan"}) }
        else if (e.event === "busy") { busy = e.busy; busyAction = e.busy ? e.action : ""; if (!busy && ["cast", "audio_delay"].indexOf(e.action) >= 0) { progress = -1; preparing = false } }
        else if (e.event === "phase") {
            phaseName = e.phase; phaseMessage = e.message
            preparing = ["connecting", "compatibility", "subtitles", "buffering", "loading"].indexOf(e.phase) >= 0
        } else if (e.event === "profile") profileDescription = e.description
        else if (e.event === "pickerClosed") root.open()
        else if (e.event === "commandResult" && e.id === pendingId) clearPending()

        else if (e.event === "devices") {
            devices = e.devices
            if (devices.length && !devices.some(function(d) { return d.value === receiver.value })) receiver.value = devices[0].value
            message = devices.length ? "Ready to cast" : "Turn on your TV and connect it to the same Wi-Fi."
            failed = false
        } else if (e.event === "tracks") {
            sourceDuration = e.duration
            audioTracks = [{value: "default", label: "Default audio"}].concat(e.audio)
            subtitleTracks = [{value: "none", label: "Off"}, {value: "external", label: "External subtitle file…"}].concat(e.subtitles)
            message = "Video ready · " + clock(e.duration) + (e.omittedSubtitles ? " · Image subtitles need an external SRT/VTT file" : "")
            failed = false
        } else if (e.event === "picked") {
            if (e.kind === "subtitle") { external.text = e.path; subtitles.value = "external" }
            else chooseSource(e.path)
        } else if (e.event === "status") playback = e
        else if (e.event === "progress") progress = e.percent
        else if (e.event === "error" || e.event === "message") { message = e.message; failed = e.event === "error"; if (failed) { preparing = false; phaseName = "idle"; clearPending() } }
    }
    function clearPending() {
        pendingAction = ""; pendingState = ""; pendingPosition = -1; pendingVolume = -1; pendingMuted = null
        controlTimeout.stop()
    }
    Timer {
        id: controlTimeout
        onTriggered: {
            root.clearPending()
            root.message = "The TV is taking longer to respond. You can try again."
            root.failed = true
        }
    }
    Process {
        id: worker
        command: ["python", root.pluginRoot + "/backend.py"]
        running: true
        stdinEnabled: true
        stdout: SplitParser { onRead: data => root.receive(data) }
        stderr: SplitParser { onRead: data => console.warn("Omarchy Cast:", data) }
        onExited: function(code) {
            root.ready = false; root.busy = false
            if (root.exiting) root.disable()
            else { root.failed = true; root.message = "Cast worker stopped (" + code + "). Exit and reopen the app." }
        }
    }
    Timer { id: exitTimeout; interval: 12000; onTriggered: root.disable() }
    IpcHandler {
        target: root.ipcTarget
        function open(): void { root.contextMode = false; root.open() }
        function toggle(): void { root.contextMode = false; root.toggle() }
        function close(): void { root.close() }
        function scan(): void { if (!root.busy) root.send({action: "scan"}) }
        function openVideo(path: string): void { root.contextMode = false; root.chooseSource(path); root.open() }
        function useCompatibility(): void { mode.value = "convert" }
        function setAudioDelay(milliseconds: int): void { root.audioDelayMs = Math.max(-1000, Math.min(1000, milliseconds)) }
        function resumeAt(seconds: real): void { start.text = String(Math.max(0, Math.floor(seconds))) }
        function castVideo(): void { if (!root.busy && root.sourcePath) root.cast() }
        function options(): void { root.optionsExpanded = true; root.open() }
        function contextMenu(): void { root.contextMode = true; root.open() }
        function exit(): void { root.exitApp() }
        function status(): string { return JSON.stringify({ready: root.ready, busy: root.busy, message: root.message, phase: root.phaseName, profile: root.profileDescription, pendingAction: root.pendingAction, devices: root.devices, playback: root.playback, opened: root.opened}) }
    }
    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: "󰄙"
        active: root.playback.connected
        activeColor: Color.accent
        tooltipText: "Omarchy Cast" + (root.playback.connected ? " · " + root.playback.device : "") + "\nLeft-click to open · Right-click to exit"
        onPressed: function(code) {
            if (code === Qt.RightButton) { root.contextMode = true; root.open() }
            else if (code === Qt.LeftButton) {
                if (root.contextMode) { root.contextMode = false; root.open() }
                else root.toggle()
            }
        }
    }
    KeyboardPanel {
        id: panel
        anchorItem: button
        owner: root
        bar: root.bar
        open: root.opened
        focusTarget: panelFocus
        padding: Style.space(24)
        contentWidth: panel.fittedContentWidth(Style.space(root.contextMode ? 220 : 420))
        Behavior on contentHeight { NumberAnimation { duration: 220; easing.type: Easing.InOutCubic } }
        contentHeight: panel.fittedContentHeight(root.contextMode ? contextColumn.implicitHeight : content.implicitHeight, Style.space(820))
        Item {
            id: panelFocus
            anchors.fill: parent
            focus: true
            Keys.onEscapePressed: root.close()
            Column {
                id: contextColumn
                visible: root.contextMode
                width: parent.width
                spacing: Style.space(8)
                Action { width: parent.width; text: "Open Cast"; leftAlign: true; onClicked: root.contextMode = false }
                PanelSeparator { width: parent.width }
                Action { width: parent.width; text: "Exit"; leftAlign: true; onClicked: root.exitApp() }
            }
            Flickable {
                anchors.fill: parent
                visible: !root.contextMode
                clip: true
                contentHeight: content.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                Controls.ScrollBar.vertical: Controls.ScrollBar { }
                ColumnLayout {
                    id: content
                    width: parent.width
                    spacing: Style.space(20)
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "Cast"; color: Color.foreground; font.family: Style.font.family; font.pixelSize: Style.space(24); font.bold: true }
                        Item { Layout.fillWidth: true }
                        Caption { text: root.playback.connected ? "ON YOUR TV" : "VIDEO, MEET TV"; font.pixelSize: Style.space(9); font.letterSpacing: Style.space(1) }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: Style.space(root.sourcePath ? 134 : 172)
                        radius: Style.cornerRadius
                        color: Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.07)
                        border.width: drop.containsDrag ? Style.space(2) : 0
                        border.color: Color.accent
                        ColumnLayout {
                            anchors.centerIn: parent
                            width: parent.width - Style.space(32)
                            spacing: Style.space(12)
                            Text {
                                Layout.alignment: Qt.AlignHCenter
                                text: root.playback.connected ? "󰄙" : "󰕧"
                                color: Color.accent
                                font.family: Style.font.family
                                font.pixelSize: Style.space(34)
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.videoName
                                textFormat: Text.PlainText
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideMiddle
                                color: Color.foreground
                                font.family: Style.font.family
                                font.pixelSize: Style.space(root.sourcePath ? 14 : 17)
                                font.bold: true
                            }
                            Caption {
                                Layout.alignment: Qt.AlignHCenter
                                text: root.sourcePath ? (root.sourceDuration ? root.clock(root.sourceDuration) + "  ·  " : "") + "Click to change" : "Drop a file here, or click to browse"
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            enabled: !root.busy
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.send({action: "pick", kind: "video"})
                        }
                        DropArea {
                            id: drop
                            anchors.fill: parent
                            onDropped: function(event) {
                                if (root.busy || !event.hasUrls || !event.urls.length) return
                                var url = event.urls[0].toString()
                                root.chooseSource(url.indexOf("file://") === 0 ? decodeURIComponent(url.substring(7)) : url)
                                event.acceptProposedAction()
                            }
                        }
                        Keys.onReturnPressed: root.send({action: "pick", kind: "video"})
                        activeFocusOnTab: !root.busy
                    }

                    Action {
                        visible: !root.sourcePath && !root.urlEntry
                        Layout.alignment: Qt.AlignHCenter
                        text: "Or paste a video link"
                        fontSize: Style.font.caption
                        enabled: !root.busy
                        onClicked: { root.urlEntry = true; linkInput.forceActiveFocus() }
                    }
                    RowLayout {
                        visible: root.urlEntry
                        Layout.fillWidth: true
                        TextField { id: linkInput; Layout.fillWidth: true; placeholderText: "https://…"; onAccepted: if (text.trim()) root.chooseSource(text.trim()) }
                        Action { text: "Add"; enabled: linkInput.text.trim() !== ""; onClicked: root.chooseSource(linkInput.text.trim()) }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Style.space(8)
                        RowLayout {
                            Layout.fillWidth: true
                            Caption { text: "PLAY ON"; font.pixelSize: Style.space(9); font.letterSpacing: Style.space(1) }
                            Item { Layout.fillWidth: true }
                            Action { text: root.busyAction === "scan" ? "Searching…" : "Refresh"; fontSize: Style.font.caption; horizontalPadding: 0; verticalPadding: 0; enabled: !root.busy; onClicked: root.send({action: "scan"}) }
                        }
                        Dropdown {
                            id: receiver
                            Layout.fillWidth: true
                            showLabel: false
                            options: root.devices.length ? root.devices : [{value: "", label: root.busy ? "Looking for your TV…" : "No TVs found"}]
                            enabled: !root.busy
                            rowHeight: Style.space(42)
                        }
                        Caption { visible: !root.devices.length && !root.busy; text: "Connect your TV to the same Wi-Fi."; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    }

                    RowLayout {
                        visible: root.sourcePath !== ""
                        Layout.fillWidth: true
                        spacing: Style.space(12)
                        Text { text: "󰨖"; color: Color.foreground; font.family: Style.font.family; font.pixelSize: Style.space(20) }
                        Dropdown { id: subtitles; Layout.fillWidth: true; showLabel: false; value: "none"; options: root.subtitleTracks; enabled: !root.busy; onChanged: function(value) { if (value === "external" && !external.text) root.send({action: "pick", kind: "subtitle"}) } }
                    }
                    RowLayout {
                        visible: subtitles.value === "external"
                        Layout.fillWidth: true
                        Caption { Layout.fillWidth: true; text: external.text ? external.text.split("/").pop() : "Choose a subtitle file"; elide: Text.ElideMiddle }
                        Action { text: "Change"; fontSize: Style.font.caption; enabled: !root.busy; onClicked: root.send({action: "pick", kind: "subtitle"}) }
                    }
                    TextField { id: external; visible: false }

                    ColumnLayout {
                        visible: root.transitioning
                        Layout.fillWidth: true
                        spacing: Style.space(10)
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Style.space(12)
                            Item {
                                implicitWidth: Style.space(24)
                                implicitHeight: Style.space(24)
                                Text {
                                    anchors.centerIn: parent
                                    text: "󰔟"
                                    color: Color.accent
                                    font.family: Style.font.family
                                    font.pixelSize: Style.space(22)
                                    RotationAnimator on rotation { from: 0; to: 360; duration: 1600; loops: Animation.Infinite; running: root.transitioning && root.opened }
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Style.space(5)
                                Text { text: root.preparing ? root.phaseMessage : "Buffering…"; Layout.fillWidth: true; wrapMode: Text.Wrap; color: Color.foreground; font.family: Style.font.family; font.pixelSize: Style.font.body }
                                Caption { text: root.profileDescription || "A moment for the best picture and sound."; Layout.fillWidth: true; wrapMode: Text.Wrap }
                            }
                        }
                        Rectangle {
                            id: bufferTrack
                            Layout.fillWidth: true
                            implicitHeight: Style.space(3)
                            radius: height / 2
                            clip: true
                            color: Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.10)
                            Rectangle {
                                width: parent.width * 0.28
                                height: parent.height
                                radius: height / 2
                                color: Color.accent
                                SequentialAnimation on x {
                                    running: root.transitioning && root.opened
                                    loops: Animation.Infinite
                                    NumberAnimation { from: -bufferTrack.width * 0.28; to: bufferTrack.width; duration: 1400; easing.type: Easing.InOutSine }
                                }
                            }
                        }
                        Action { visible: root.preparing; text: "Cancel"; Layout.alignment: Qt.AlignHCenter; onClicked: root.send({action: "cancel"}) }
                    }
                    Action {
                        visible: !root.preparing && !root.playback.connected
                        Layout.fillWidth: true
                        text: "Play on " + root.receiverName
                        iconText: "󰐊"
                        fontSize: Style.space(14)
                        verticalPadding: Style.space(14)
                        bordered: true
                        background: Color.accent
                        foreground: Color.background
                        accent: Color.foreground
                        enabled: root.ready && !root.busy && root.sourcePath !== "" && (receiver.value !== "" || host.text.trim() !== "")
                        onClicked: root.cast()
                    }
                    Caption { visible: root.failed; text: root.message; opacity: 1; color: Color.urgent; Layout.fillWidth: true; wrapMode: Text.Wrap }

                    ColumnLayout {
                        visible: !!root.playback.connected && !root.preparing
                        Layout.fillWidth: true
                        spacing: Style.space(12)
                        RowLayout {
                            Layout.fillWidth: true
                            Caption { text: root.clock(root.displayedPosition) }
                            PanelSlider { Layout.fillWidth: true; bar: root.bar; maximum: Math.max(1, root.playback.duration || 0); value: root.displayedPosition; step: 1; enabled: !root.busy && !root.controlPending; onReleased: value => root.send({action: "seek", value: value}) }
                            Caption { text: root.clock(root.playback.duration) }
                        }
                        RowLayout {
                            Layout.alignment: Qt.AlignHCenter
                            spacing: Style.space(24)
                            enabled: !root.busy && !root.controlPending
                            Action { text: "−30"; tooltipText: "Back 30 seconds"; onClicked: root.send({action: "seek", value: -30, relative: true}) }
                            Action { iconText: root.displayedState === "PLAYING" ? "󰏤" : "󰐊"; iconSize: Style.space(28); tooltipText: root.displayedState === "PLAYING" ? "Pause" : "Play"; onClicked: root.send({action: "pause"}) }
                            Action { text: "+30"; tooltipText: "Forward 30 seconds"; onClicked: root.send({action: "seek", value: 30, relative: true}) }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            enabled: !root.busy && !root.controlPending
                            Action { iconText: root.displayedMuted ? "󰝟" : "󰕾"; tooltipText: root.displayedMuted ? "Unmute" : "Mute"; onClicked: root.send({action: "mute"}) }
                            PanelSlider { Layout.fillWidth: true; bar: root.bar; value: root.displayedVolume; onReleased: value => root.send({action: "volume", value: value}) }
                            Action { text: "CC"; active: root.subtitlesEnabled; visible: root.loadedSubtitle !== "none"; tooltipText: "Toggle subtitles"; onClicked: { root.subtitlesEnabled = !root.subtitlesEnabled; root.send({action: "subtitles", enabled: root.subtitlesEnabled}) } }
                        }
                        RowLayout {
                            Layout.alignment: Qt.AlignHCenter
                            visible: root.controlPending
                            Repeater {
                                model: 3
                                Rectangle {
                                    required property int index
                                    width: Style.space(4); height: width; radius: width / 2; color: Color.accent
                                    SequentialAnimation on opacity {
                                        running: root.controlPending && root.opened
                                        loops: Animation.Infinite
                                        PauseAnimation { duration: index * 100 }
                                        NumberAnimation { to: 0.25; duration: 260 }
                                        NumberAnimation { to: 1; duration: 260 }
                                        PauseAnimation { duration: (2 - index) * 100 }
                                    }
                                }
                            }
                            Caption { text: root.pendingAction === "seek" ? "Seeking…" : "Updating TV…" }
                        }
                        Action { text: "Stop casting"; fontSize: Style.font.caption; Layout.alignment: Qt.AlignHCenter; enabled: !root.busy; onClicked: root.send({action: "stop"}) }
                    }

                    PanelSeparator { Layout.fillWidth: true }
                    RowLayout {
                        Layout.fillWidth: true
                        Caption { text: root.playback.connected ? "Playing on " + root.playback.device : "Make yourself comfortable."; Layout.fillWidth: true; elide: Text.ElideRight }
                        Action { text: "Options"; iconText: root.optionsExpanded ? "󰅃" : "󰅀"; fontSize: Style.font.caption; horizontalPadding: 0; verticalPadding: 0; onClicked: root.optionsExpanded = !root.optionsExpanded }
                    }

                    ColumnLayout {
                        visible: root.optionsExpanded
                        Layout.fillWidth: true
                        spacing: Style.space(16)
                        Dropdown { id: audio; Layout.fillWidth: true; label: "Audio"; value: "default"; options: root.audioTracks; enabled: !root.busy }
                        RowLayout {
                            visible: subtitles.value !== "none"
                            Layout.fillWidth: true
                            Dropdown { id: subtitleSize; label: "Subtitle size"; value: "1"; options: [{value: "0.8", label: "Small"}, {value: "1", label: "Normal"}, {value: "1.4", label: "Large"}, {value: "1.8", label: "Extra large"}]; Layout.fillWidth: true; enabled: !root.busy }
                            ColumnLayout {
                                Layout.fillWidth: true
                                Caption { text: "Language" }
                                TextField { id: language; text: "en"; placeholderText: "en"; Layout.fillWidth: true; enabled: !root.busy }
                            }
                        }
                        Dropdown { id: mode; Layout.fillWidth: true; label: "Compatibility"; value: "auto"; options: [{value: "auto", label: "Automatic"}, {value: "direct", label: "Play original file"}, {value: "convert", label: "Convert for this TV"}]; enabled: !root.busy }
                        Caption { visible: mode.value !== "direct"; text: "Compatible video stays unchanged. When needed, conversion runs live with a small buffer."; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Dropdown {
                            id: deviceProfile; Layout.fillWidth: true; label: "Device model"; value: "auto"; enabled: !root.busy
                            options: [
                                {value: "auto", label: "Detect automatically"},
                                {value: "chromecast12", label: "Chromecast 1st / 2nd generation"},
                                {value: "chromecast3", label: "Chromecast 3rd generation"},
                                {value: "ultra", label: "Chromecast Ultra"},
                                {value: "googletv4k", label: "Chromecast with Google TV (4K)"},
                                {value: "googletvhd", label: "Chromecast with Google TV (HD)"},
                                {value: "streamer", label: "Google TV Streamer"},
                                {value: "nesthub", label: "Nest Hub"},
                                {value: "nesthubmax", label: "Nest Hub Max"}
                            ]
                        }
                        Caption { text: "Choose the model if your TV only identifies itself as Chromecast."; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Dropdown { id: quality; Layout.fillWidth: true; label: "Video quality"; value: "original"; options: [{value: "original", label: "Best for this TV"}, {value: "2160", label: "Up to 4K"}, {value: "1080", label: "Up to 1080p"}, {value: "720", label: "Up to 720p"}]; enabled: !root.busy && mode.value !== "direct" }
                        Dropdown { id: sound; Layout.fillWidth: true; label: "Sound"; value: "stereo"; options: [{value: "stereo", label: "High-quality stereo"}, {value: "surround", label: "Surround 5.1"}]; enabled: !root.busy }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Style.space(8)
                            RowLayout {
                                Layout.fillWidth: true
                                Caption { text: "Audio delay" }
                                Item { Layout.fillWidth: true }
                                Caption { text: root.audioDelayMs === 0 ? "No adjustment · 0 ms" : Math.abs(root.audioDelayMs) + " ms " + (root.audioDelayMs < 0 ? "earlier" : "later") }
                            }
                            DelaySlider {
                                Layout.fillWidth: true
                                bar: root.bar
                                minimum: -1000
                                maximum: 1000
                                step: 50
                                integer: true
                                value: root.audioDelayMs
                                enabled: !root.busy && !root.controlPending
                                onMoved: value => root.audioDelayMs = Math.round(value / 50) * 50
                                onReleased: value => { root.audioDelayMs = Math.round(value / 50) * 50; root.applyAudioDelay() }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Caption { text: "1 s earlier" }
                                Item { Layout.fillWidth: true }
                                Action { text: "Reset"; fontSize: Style.font.caption; verticalPadding: 0; enabled: root.audioDelayMs !== 0 && !root.busy && !root.controlPending; onClicked: { root.audioDelayMs = 0; root.applyAudioDelay() } }
                                Item { Layout.fillWidth: true }
                                Caption { text: "1 s later" }
                            }
                            Caption { text: root.pendingAction === "audio_delay" ? "Adjusting audio… Your video will briefly rebuffer." : "Sound behind the picture? Drag left. Release to apply automatically with a brief rebuffer."; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        }
                        Caption { text: root.profileDescription; visible: text !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        TextField { id: start; Layout.fillWidth: true; placeholderText: "Start at (seconds)"; validator: DoubleValidator { bottom: 0 } enabled: !root.busy }
                        TextField { id: host; Layout.fillWidth: true; placeholderText: "TV IP address (optional)"; enabled: !root.busy }
                        Caption { visible: host.text.trim() !== ""; text: "Using this address instead of the selected TV."; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Action { text: "Use a video link"; enabled: !root.busy; onClicked: { root.urlEntry = true; linkInput.forceActiveFocus() } }
                        Action { visible: !!root.playback.connected; text: "Apply & restart video"; bordered: true; Layout.fillWidth: true; enabled: !root.busy; onClicked: { start.text = String(Math.floor(root.playback.position || 0)); root.cast() } }
                        Caption { visible: !!root.playback.connected; text: "Track and quality changes apply when the video restarts."; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    }
                }
            }
        }
    }
}
