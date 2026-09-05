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
    property int progress: -1
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
    }
    function chooseSource(path) {
        sourcePath = path
        sourceDuration = 0
        urlEntry = false
        inspectSource()
    }

    function send(command) {
        if (!ready) { message = "Cast worker is not running. Exit and reopen Omarchy Cast."; failed = true; return }
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
              quality: quality.value, start: Number(start.text) || 0})
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
        else if (e.event === "busy") { busy = e.busy; if (!busy) { progress = -1; preparing = false } }
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
        else if (e.event === "error" || e.event === "message") { message = e.message; failed = e.event === "error" }
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
        function castVideo(): void { if (!root.busy && root.sourcePath) root.cast() }
        function options(): void { root.optionsExpanded = true; root.open() }
        function contextMenu(): void { root.contextMode = true; root.open() }
        function exit(): void { root.exitApp() }
        function status(): string { return JSON.stringify({ready: root.ready, busy: root.busy, message: root.message, devices: root.devices, playback: root.playback, opened: root.opened}) }
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
                            Action { text: root.busy && !root.preparing ? "Searching…" : "Refresh"; fontSize: Style.font.caption; horizontalPadding: 0; verticalPadding: 0; enabled: !root.busy; onClicked: root.send({action: "scan"}) }
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
                        visible: root.preparing
                        Layout.fillWidth: true
                        spacing: Style.space(10)
                        RowLayout {
                            Layout.fillWidth: true
                            Caption { text: root.progress >= 0 ? "Preparing your video" : "Connecting to " + root.receiverName; Layout.fillWidth: true; elide: Text.ElideRight }
                            Caption { text: root.progress >= 0 ? root.progress + "%" : "" }
                        }
                        Rectangle {
                            Layout.fillWidth: true; implicitHeight: Style.space(3); radius: height / 2
                            color: Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.12)
                            Rectangle { width: parent.width * Math.max(0.03, root.progress / 100); height: parent.height; radius: height / 2; color: Color.accent }
                        }
                        Action { text: "Cancel"; Layout.alignment: Qt.AlignHCenter; onClicked: root.send({action: "cancel"}) }
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
                            Caption { text: root.clock(root.playback.position) }
                            PanelSlider { Layout.fillWidth: true; bar: root.bar; maximum: Math.max(1, root.playback.duration || 0); value: root.playback.position || 0; step: 1; enabled: !root.busy; onReleased: value => root.send({action: "seek", value: value}) }
                            Caption { text: root.clock(root.playback.duration) }
                        }
                        RowLayout {
                            Layout.alignment: Qt.AlignHCenter
                            spacing: Style.space(24)
                            enabled: !root.busy
                            Action { text: "−30"; tooltipText: "Back 30 seconds"; onClicked: root.send({action: "seek", value: -30, relative: true}) }
                            Action { iconText: root.playback.state === "PLAYING" ? "󰏤" : "󰐊"; iconSize: Style.space(28); tooltipText: root.playback.state === "PLAYING" ? "Pause" : "Play"; onClicked: root.send({action: "pause"}) }
                            Action { text: "+30"; tooltipText: "Forward 30 seconds"; onClicked: root.send({action: "seek", value: 30, relative: true}) }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            enabled: !root.busy
                            Action { iconText: root.playback.muted ? "󰝟" : "󰕾"; tooltipText: root.playback.muted ? "Unmute" : "Mute"; onClicked: root.send({action: "mute"}) }
                            PanelSlider { Layout.fillWidth: true; bar: root.bar; value: root.playback.volume === undefined ? 0.5 : root.playback.volume; onReleased: value => root.send({action: "volume", value: value}) }
                            Action { text: "CC"; active: root.subtitlesEnabled; visible: root.loadedSubtitle !== "none"; tooltipText: "Toggle subtitles"; onClicked: { root.subtitlesEnabled = !root.subtitlesEnabled; root.send({action: "subtitles", enabled: root.subtitlesEnabled}) } }
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
                        Caption { visible: mode.value !== "direct"; text: "If needed, your video is prepared before playback. Large files can take a few minutes."; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Dropdown { id: quality; Layout.fillWidth: true; label: "Video quality"; value: "original"; options: [{value: "original", label: "Original resolution"}, {value: "1080", label: "Up to 1080p"}, {value: "720", label: "Up to 720p"}]; enabled: !root.busy && mode.value !== "direct" }
                        TextField { id: start; Layout.fillWidth: true; placeholderText: "Start at (seconds)"; validator: DoubleValidator { bottom: 0 } enabled: !root.busy }
                        TextField { id: host; Layout.fillWidth: true; placeholderText: "TV IP address (optional)"; enabled: !root.busy }
                        Caption { visible: host.text.trim() !== ""; text: "Using this address instead of the selected TV."; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Action { text: "Use a video link"; enabled: !root.busy; onClicked: { root.urlEntry = true; linkInput.forceActiveFocus() } }
                        Action { visible: !!root.playback.connected; text: "Apply & restart video"; bordered: true; Layout.fillWidth: true; enabled: !root.busy; onClicked: root.cast() }
                        Caption { visible: !!root.playback.connected; text: "Track and quality changes apply when the video restarts."; Layout.fillWidth: true; wrapMode: Text.Wrap }
                    }
                }
            }
        }
    }
}
