"""Check the Windows ABI and fail-closed backdrop activation without a desktop."""
import ctypes
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from app import widget_backdrop as backdrop
except ImportError:
    print("FAIL native backdrop implementation is missing")
    raise SystemExit(1)

calls = []
def compose(hwnd, pointer):
    data = ctypes.cast(pointer, ctypes.POINTER(backdrop._Composition)).contents
    policy = ctypes.cast(data.data, ctypes.POINTER(backdrop._Accent)).contents
    calls.append((hwnd.value, data.attribute, data.size, policy.state, policy.color))
    return 1

api = Mock(SetWindowCompositionAttribute=compose)
with patch.object(backdrop.ctypes, "WinDLL", return_value=api, create=True):
    assert backdrop._apply(0x123456789), "native API should accept a pointer-sized HWND"
assert calls == [(0x123456789, 19, ctypes.sizeof(backdrop._Accent), 4, 0x30281E18)], calls
with patch.object(backdrop.ctypes, "WinDLL", side_effect=OSError("unavailable"), create=True):
    assert not backdrop._apply(123), "missing compositor must select readable fallback"
api.SetWindowCompositionAttribute = Mock(return_value=0)
with patch.object(backdrop.ctypes, "WinDLL", return_value=api, create=True):
    assert not backdrop._apply(123), "a rejected composition request must not enable clear paint"
print("PASS native payload, pointer size, unavailable API, and rejected activation")
