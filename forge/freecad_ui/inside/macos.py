"""macOS only: stop the system from slowing down our hidden window. Runs inside FreeCAD."""

from __future__ import annotations

import sys


def no_app_nap() -> bool:
    """Ask macOS, for THIS process only, to treat it as busy with something the user wants.

    macOS demotes an app whose windows are hidden: about 30 seconds after launch its
    priority drops (measured: `ps` priority 46 -> 28), its timers are delayed by
    100-300 ms and its code runs 3 to 20 times slower. Everything still works, at a
    tenth of the speed. The cure is an "activity" held for the life of the process:
    NSProcessInfo.beginActivityWithOptions:reason: with
        NSActivityUserInitiated   0x00FFFFFF   no App Nap, no timer throttling
        NSActivityLatencyCritical 0xFF00000000 timers fire on time
    minus bit 20 (NSActivityIdleSystemSleepDisabled), so the Mac may still go to sleep.
    It is reached through the Objective-C runtime with ctypes (FreeCAD's Python has no
    PyObjC). Each call gets an exact C prototype: on Apple Silicon objc_msgSend must be
    called with the real argument types, never as a variadic function.
    """
    if sys.platform != "darwin":
        return False
    import ctypes
    import ctypes.util

    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    pointer = ctypes.c_void_p

    def send(*argument_types):
        """objc_msgSend typed as (receiver, selector, *arguments) -> pointer."""
        return ctypes.CFUNCTYPE(pointer, pointer, pointer, *argument_types)(("objc_msgSend", objc))

    selector = objc.sel_registerName
    info = send()(objc.objc_getClass(b"NSProcessInfo"), selector(b"processInfo"))
    reason = send(ctypes.c_char_p)(objc.objc_getClass(b"NSString"),
                                   selector(b"stringWithUTF8String:"), b"forge ui runtime")
    options = (0x00FFFFFF | 0xFF00000000) & ~(1 << 20)
    token = send(ctypes.c_uint64, pointer)(info, selector(b"beginActivityWithOptions:reason:"),
                                           options, reason)
    if not token:
        return False
    send()(token, selector(b"retain"))      # keep the activity for the life of the process
    return True


def no_dock_icon() -> bool:
    """Take this process out of the Dock and the app switcher (macOS).

    NSApplication.setActivationPolicy:(NSApplicationActivationPolicyAccessory = 1): the
    app keeps running and keeps its windows, but has no Dock icon, no place in Cmd-Tab
    and no menu bar, so it cannot be brought to the front or quit by accident. (The
    icon can still flash for the second or two before this runs at start.)
    """
    if sys.platform != "darwin":
        return False
    import ctypes
    import ctypes.util

    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    ctypes.cdll.LoadLibrary(ctypes.util.find_library("AppKit"))
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    pointer = ctypes.c_void_p
    shared = ctypes.CFUNCTYPE(pointer, pointer, pointer)(("objc_msgSend", objc))
    app = shared(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
    set_policy = ctypes.CFUNCTYPE(ctypes.c_bool, pointer, pointer, ctypes.c_long)(
        ("objc_msgSend", objc))
    return bool(set_policy(app, objc.sel_registerName(b"setActivationPolicy:"), 1))
