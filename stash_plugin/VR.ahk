#Requires AutoHotkey v2.0
#SingleInstance Force

global VRMode := false
global CursorHidden := false
global TransparentCursor := 0

#HotIf WinActive("ahk_exe chrome.exe")

; ==========================================
; VR MODE
; Mouse Back = XButton1
; ==========================================
XButton1::
{
    global VRMode

    VRMode := !VRMode

    if VRMode
    {
        HideCursor()

        ; Start VR drag
        SendEvent "{Blind}{LButton Down}"

        ToolTip("VR MODE : ON")
    }
    else
    {
        ; Release VR drag
        SendEvent "{Blind}{LButton Up}"

        ; Restore normal cursor
        ShowCursor()

        ; Reset VR zoom
        SendEvent "{F6}"

        ToolTip("VR MODE : OFF")
    }

    SetTimer(RemoveTip, -1000)
}


; ==========================================
; STASH CONTROLS
; Mouse Forward = XButton2
; ==========================================
XButton2::
{
    SendEvent "{F3}"
}


; ==========================================
; ZOOM IN
; Wheel Up = F4
; ==========================================
WheelUp::
{
    global VRMode

    if VRMode
        SendEvent "{F4}"
    else
        SendEvent "{WheelUp}"
}


; ==========================================
; ZOOM OUT
; Wheel Down = F5
; ==========================================
WheelDown::
{
    global VRMode

    if VRMode
        SendEvent "{F5}"
    else
        SendEvent "{WheelDown}"
}


; ==========================================
; EMERGENCY VR RELEASE
; Esc
; ==========================================
Esc::
{
    global VRMode

    if VRMode
    {
        VRMode := false

        SendEvent "{Blind}{LButton Up}"

        ShowCursor()

        ; Reset VR zoom
        SendEvent "{F6}"

        ToolTip("VR MODE : OFF")
        SetTimer(RemoveTip, -1000)
    }
}


; ==========================================
; CREATE TRANSPARENT CURSOR
; ==========================================
CreateTransparentCursor()
{
    global TransparentCursor

    ; 32x32 monochrome cursor masks
    ; AND mask = all 1
    ; XOR mask = all 0
    ; Result = completely transparent

    andMask := Buffer(128, 0xFF)
    xorMask := Buffer(128, 0x00)

    TransparentCursor := DllCall(
        "CreateCursor",
        "Ptr", 0,
        "Int", 0,
        "Int", 0,
        "Int", 32,
        "Int", 32,
        "Ptr", andMask.Ptr,
        "Ptr", xorMask.Ptr,
        "Ptr"
    )

    return TransparentCursor
}


; ==========================================
; HIDE CURSOR
; ==========================================
HideCursor()
{
    global CursorHidden
    global TransparentCursor

    if CursorHidden
        return

    if !TransparentCursor
        CreateTransparentCursor()

    ; Replace the normal Windows arrow cursor
    ; with our transparent cursor.
    DllCall(
        "SetSystemCursor",
        "Ptr", TransparentCursor,
        "UInt", 32512 ; OCR_NORMAL
    )

    TransparentCursor := 0
    CursorHidden := true
}


; ==========================================
; RESTORE NORMAL WINDOWS CURSORS
; ==========================================
ShowCursor()
{
    global CursorHidden

    if !CursorHidden
        return

    ; SPI_SETCURSORS = 0x0057
    ; Reloads Windows' default system cursors.
    DllCall(
        "SystemParametersInfo",
        "UInt", 0x0057,
        "UInt", 0,
        "Ptr", 0,
        "UInt", 0
    )

    CursorHidden := false
}


; ==========================================
; SAFETY RESTORE IF AHK EXITS
; ==========================================
RestoreCursor(*)
{
    global CursorHidden

    if CursorHidden
        ShowCursor()
}

OnExit(RestoreCursor)


RemoveTip()
{
    ToolTip()
}

#HotIf