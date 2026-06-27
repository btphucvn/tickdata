"""
Bảng tra đặc tả symbol (Phụ lục A của kiến trúc).

Mỗi symbol có:
  * ``digits``       : số chữ số thập phân giá MT4 hiển thị (EURUSD=5, USDJPY=3...).
  * ``point_factor`` : 10**digits — dùng để decode giá nguyên của Dukascopy ra giá thật.
  * ``point``        : giá trị 1 point = 10**(-digits) (đơn vị spread trong MT4).

Dukascopy lưu giá dưới dạng số nguyên = giá_thật * point_factor, nên:
        giá_thật = raw_int / point_factor

Lưu ý: nhiều symbol "5 digits" thực ra MT4 đôi khi gọi point theo pip khác nhau,
nhưng để build FXT ta chỉ cần digits/point_factor nhất quán với data nguồn.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolSpec:
    """Đặc tả một symbol forex/CFD cho việc decode & build FXT."""

    name: str           # ví dụ "EURUSD"
    digits: int         # số chữ số thập phân (point digits)
    base: str = ""      # base currency (3 ký tự đầu), tự suy nếu rỗng
    quote: str = ""     # quote currency (3 ký tự sau), tự suy nếu rỗng

    @property
    def point_factor(self) -> int:
        """10**digits — hệ số nhân để chuyển giá thật <-> giá nguyên Dukascopy."""
        return 10 ** self.digits

    @property
    def point(self) -> float:
        """Giá trị 1 point = 10**(-digits)."""
        return 10.0 ** (-self.digits)

    @property
    def base_currency(self) -> str:
        return self.base or self.name[:3]

    @property
    def quote_currency(self) -> str:
        return self.quote or self.name[3:6]


# Bảng tra mặc định — bổ sung symbol bạn dùng vào đây.
# (digits theo chuẩn Dukascopy / MT4 5-digit phổ biến.)
_DEFAULT_SPECS: dict[str, SymbolSpec] = {
    "EURUSD": SymbolSpec("EURUSD", 5),
    "GBPUSD": SymbolSpec("GBPUSD", 5),
    "AUDUSD": SymbolSpec("AUDUSD", 5),
    "NZDUSD": SymbolSpec("NZDUSD", 5),
    "USDCAD": SymbolSpec("USDCAD", 5),
    "USDCHF": SymbolSpec("USDCHF", 5),
    "EURGBP": SymbolSpec("EURGBP", 5),
    "EURJPY": SymbolSpec("EURJPY", 3),
    "GBPJPY": SymbolSpec("GBPJPY", 3),
    "USDJPY": SymbolSpec("USDJPY", 3),
    "XAUUSD": SymbolSpec("XAUUSD", 3),   # vàng
    "XAGUSD": SymbolSpec("XAGUSD", 3),   # bạc
}


def get_symbol_spec(symbol: str) -> SymbolSpec:
    """
    Trả về :class:`SymbolSpec` cho ``symbol``.

    Nếu symbol chưa có trong bảng, ta đoán heuristic: cặp chứa "JPY" hoặc kim loại
    -> 3 digits, còn lại -> 5 digits. Vẫn nên thêm symbol thật vào ``_DEFAULT_SPECS``
    để chắc chắn.
    """
    key = symbol.upper()
    if key in _DEFAULT_SPECS:
        return _DEFAULT_SPECS[key]

    # Heuristic dự phòng cho symbol lạ.
    digits = 3 if ("JPY" in key or key.startswith(("XAU", "XAG"))) else 5
    return SymbolSpec(key, digits)


def register_symbol(spec: SymbolSpec) -> None:
    """Cho phép người dùng/GUI đăng ký thêm symbol lúc runtime."""
    _DEFAULT_SPECS[spec.name.upper()] = spec


def known_symbols() -> list[str]:
    """Danh sách symbol đã biết (cho dropdown GUI)."""
    return sorted(_DEFAULT_SPECS)
