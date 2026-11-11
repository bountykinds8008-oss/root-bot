def ema(values, n):
    k = 2 / (n + 1)
    out, e = [], None
    for v in values:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, n=14):
    out = [50.0] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n, len(closes)):
        if i > n:
            d = closes[i] - closes[i - 1]
            ag = (ag * (n - 1) + max(d, 0)) / n
            al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr(bars, n=14):
    trs, out, a = [], [], None
    for i, (o, h, l, c, *_rest) in enumerate(bars):
        pc = bars[i - 1][3] if i else c
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
        if i < n:
            a = sum(trs) / len(trs)
        else:
            a = (a * (n - 1) + tr) / n
        out.append(a)
    return out
