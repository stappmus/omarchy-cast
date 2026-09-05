import QtQuick
import qs.Commons

Item {
    id: root
    property QtObject bar: null
    property real minimum: -1000
    property real maximum: 1000
    property real step: 50
    property bool integer: true
    property real value: 0
    signal moved(real value)
    signal released(real value)
    implicitWidth: Style.space(200)
    implicitHeight: Style.spacing.controlHeight
    opacity: enabled ? 1 : 0.4
    readonly property real knobSize: Math.max(14, Style.space(16))
    readonly property real travel: Math.max(1, width - knobSize)
    Rectangle {
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width
        height: Style.space(4)
        radius: height / 2
        color: Style.selectedFillFor(Color.foreground, Color.accent)
    }
    Rectangle {
        anchors.centerIn: parent
        width: Style.space(2)
        height: Style.space(10)
        color: Color.foreground
        opacity: 0.4
    }
    Rectangle {
        id: knob
        x: root.travel * (root.value - root.minimum) / (root.maximum - root.minimum)
        anchors.verticalCenter: parent.verticalCenter
        width: root.knobSize
        height: width
        radius: width / 2
        color: Color.foreground
        scale: pointer.pressed ? 1.15 : 1
        Behavior on scale { NumberAnimation { duration: 110 } }
    }
    MouseArea {
        id: pointer
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        property real pressX: 0
        property real pressY: 0
        property real startValue: 0
        property bool horizontalDrag: false
        onPressed: mouse => {
            // Only a deliberate horizontal drag of the handle changes the value.
            if (Math.abs(mouse.x - knob.x - knob.width / 2) > knob.width) {
                mouse.accepted = false
                return
            }
            pressX = mouse.x; pressY = mouse.y; startValue = root.value
            horizontalDrag = false
        }
        onPositionChanged: mouse => {
            if (!pressed) return
            var dx = mouse.x - pressX
            var dy = mouse.y - pressY
            if (!horizontalDrag) {
                if (Math.abs(dx) < Style.space(6) || Math.abs(dx) <= Math.abs(dy)) return
                horizontalDrag = true
                preventStealing = true
            }
            var next = startValue + dx / root.travel * (root.maximum - root.minimum)
            next = Math.round(next / root.step) * root.step
            root.moved(Math.max(root.minimum, Math.min(root.maximum, next)))
        }
        onReleased: {
            if (horizontalDrag) root.released(root.value)
            horizontalDrag = false
            preventStealing = false
        }
        onCanceled: {
            if (horizontalDrag) root.moved(startValue)
            horizontalDrag = false
            preventStealing = false
        }
        onWheel: wheel => { wheel.accepted = false }
    }
}
