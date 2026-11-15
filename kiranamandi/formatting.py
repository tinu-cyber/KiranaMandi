"""Display helpers shared by templates and notification messages."""


def inr(value, decimals=0):
    """Format a number as rupees with Indian digit grouping, e.g. 123456 -> ₹1,23,456."""
    if value is None:
        return "–"
    negative = value < 0
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    out = "₹" + whole + ("." + frac if frac else "")
    return "−" + out if negative else out


def inr1(value):
    return inr(value, 1)


def pct(value, decimals=1):
    """Signed percentage, e.g. 3.14 -> +3.1%."""
    if value is None:
        return "–"
    if round(value, decimals) == 0:
        return f"{0:.{decimals}f}%"
    sign = "+" if value > 0 else "−"
    return f"{sign}{abs(value):.{decimals}f}%"


def change_class(value, threshold=0.05):
    """CSS class for a price change. A rise is bad news for a buyer, so it gets the warm colour."""
    if value is None or abs(value) <= threshold:
        return "chg-flat"
    return "chg-up" if value > 0 else "chg-down"
