"""
Window spy v2 — tim chinh xac parent panel cua tester va toa do client-relative.
"""

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes  = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def get_text(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value

def get_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value

def get_id(hwnd):
    return user32.GetWindowLongW(hwnd, -12)  # GWL_ID

def get_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r

def screen_to_client(parent, r):
    pt1 = wintypes.POINT(r.left, r.top)
    pt2 = wintypes.POINT(r.right, r.bottom)
    user32.ScreenToClient(parent, ctypes.byref(pt1))
    user32.ScreenToClient(parent, ctypes.byref(pt2))
    return pt1.x, pt1.y, pt2.x, pt2.y


def find_hwnd_by_text(root, target_text):
    """Tim control con co text == target_text."""
    found = []
    def cb(hwnd, lparam):
        if get_text(hwnd) == target_text:
            found.append(hwnd)
        return True
    user32.EnumChildWindows(root, EnumProc(cb), 0)
    return found[0] if found else None


def immediate_children(parent):
    """Chi lay con TRUC TIEP (GetWindow GW_CHILD + GW_HWNDNEXT)."""
    GW_CHILD = 5
    GW_HWNDNEXT = 2
    out = []
    child = user32.GetWindow(parent, GW_CHILD)
    while child:
        out.append(child)
        child = user32.GetWindow(child, GW_HWNDNEXT)
    return out


def main():
    # Tim MT4 main
    main_hwnd = []
    def cb(hwnd, lparam):
        if 'MetaQuotes' in get_class(hwnd):
            main_hwnd.append(hwnd)
        return True
    user32.EnumWindows(EnumProc(cb), 0)
    if not main_hwnd:
        print("MT4 chua chay")
        return
    root = main_hwnd[0]
    print(f"MT4 main: {root:#x}")

    # Tim button "Start" (id 1034) -> parent cua no la panel tester
    start = find_hwnd_by_text(root, "Start")
    if not start:
        print("Khong tim thay nut 'Start' -> Mo Strategy Tester (Ctrl+R) truoc!")
        return

    panel = user32.GetParent(start)
    print(f"\n=== PANEL TESTER ===")
    print(f"  HWND={panel:#x}  class='{get_class(panel)}'  id={get_id(panel)}")
    pr = get_rect(panel)
    print(f"  screen rect=({pr.left},{pr.top},{pr.right},{pr.bottom})  size={pr.right-pr.left}x{pr.bottom-pr.top}")

    # Liet ke con TRUC TIEP cua panel, toa do client-relative
    print(f"\n=== CON TRUC TIEP CUA PANEL (client coords) ===")
    print(f"{'id':<8}{'class':<14}{'x,y,w,h':<24}text")
    print("-" * 70)
    kids = immediate_children(panel)
    for k in kids:
        cls = get_class(k)
        txt = get_text(k)
        r   = get_rect(k)
        cx, cy, cx2, cy2 = screen_to_client(panel, r)
        w, h = cx2 - cx, cy2 - cy
        print(f"{get_id(k):<8}{cls:<14}({cx},{cy},{w},{h})".ljust(46) + f"'{txt}'")

    # Goi y vi tri trong cho chen control moi
    print(f"\n=== GOI Y CHEN CONTROL ===")
    # Tim nut Optimization (id 1029) va Use date (1023) lam moc
    for name, want_id in [("Use date", 1023), ("Optimization", 1029),
                          ("Visual mode", 1400), ("Start", 1034)]:
        for k in kids:
            if get_id(k) == want_id:
                r = get_rect(k)
                cx, cy, cx2, cy2 = screen_to_client(panel, r)
                print(f"  {name:<14} id={want_id}  client=({cx},{cy})  w={cx2-cx} h={cy2-cy}")


if __name__ == "__main__":
    main()
