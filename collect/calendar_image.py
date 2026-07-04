# -*- coding: utf-8 -*-
"""カレンダー画像ジェネレータ — sessions.json から当月の卓予定カレンダーPNGを描く
=================================================================
ボード(index.html)と同じ配色・システム色分けを踏襲した「Discordに貼れる月間カレンダー」。
日次webhook投稿(post_calendar.py)の素材。ボードはリンク先＝画像は入口の看板。

使い方: python calendar_image.py [-y 2026 -m 7] [-o calendar.png]
"""
import os, json, re, datetime, argparse

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 配色（index.htmlの:rootと同期） ----
BG = "#fbf7f0"; CARD = "#ffffff"; TX = "#3b3a36"; MUT = "#8a857c"
GRN = "#33603f"; CLN = "#e6ded2"; SUN = "#b0483a"; SAT = "#3a5f8a"
TER = "#c86c3e"

# ---- システム色分け（index.htmlのSYS_DEFSと同期） ----
AR2E_REGS = {"ミスリルクレスト", "ミスリルクエスト", "氷原開拓団", "フツウノアリアン", "イジョウナアリアン",
             "ピースメイカー", "アンコールを流星に", "さちあれ", "賽は投げられた", "ダンジョン・トラベラーズ",
             "ネームドエネミー討伐RTA!!", "勇者の首を晒せ", "グルメ卓"}
SYS_DEFS = [
    ("アリアンロッド2E", "#4f8160", re.compile(r"アリアンロッド|AR2E", re.I)),
    ("SW2.5",           "#3a5f8a", re.compile(r"SW2\.?5|ソードワールド", re.I)),
    ("CoC",             "#6d5aa0", re.compile(r"CoC|クトゥルフ", re.I)),
    ("サタスペ",         "#b0483a", re.compile(r"サタスペ")),
    ("シノビガミ",       "#4a4f68", re.compile(r"シノビガミ")),
    ("DX3rd",           "#c2527d", re.compile(r"DX3rd|ダブルクロス", re.I)),
    ("ブルアカ",         "#3aa0c8", re.compile(r"ブルアカ")),
    ("ステラナイツ",     "#b98a2f", re.compile(r"ステラナイツ")),
    ("D&D",             "#8f2f2f", re.compile(r"D&D|DnD|ダンジョンズ", re.I)),
    ("Needle",          "#4d8f8b", re.compile(r"Needle|ニードル", re.I)),
]
OTHER = "#9a948a"

def sys_color(s):
    r = (s.get("reg") or "") + " " + (s.get("scenario") or "")
    reg = s.get("reg") or ""
    if reg in AR2E_REGS or any(k in reg for k in AR2E_REGS):
        return SYS_DEFS[0][1]
    for _, c, pat in SYS_DEFS:
        if pat.search(r):
            return c
    return OTHER

def time_band(st):
    """開始時刻→asa(5-11)/hiru(12-17)/yoru(18-4)。ボード(index.html timeBand)と同期"""
    try:
        n = int(str(st).split(":")[0]) % 24
    except (ValueError, IndexError):
        return ""
    return "asa" if 5 <= n <= 11 else "hiru" if 12 <= n <= 17 else "yoru"

# 朝昼夜ミニアイコン（ボードの自前SVGと同じ配色・意匠）
ASA_C = "#c86c3e"; HIRU_C = "#b98a2f"; YORU_C = "#5b6a95"

def draw_time_icon(dr, band, x, y, s, bg):
    """s=一辺(px)。bg=チップ地の色（三日月のくり抜きに使う）"""
    if band == "asa":      # 半円の朝日＋地平線＋光線
        r = s * 0.42
        cxc, cyc = x + s / 2, y + s * 0.68
        dr.pieslice([cxc - r, cyc - r, cxc + r, cyc + r], 180, 360, fill=ASA_C)
        dr.rounded_rectangle([x, cyc - 1, x + s, cyc + 1.6], 1, fill=ASA_C)
        for ang, dx, dy in ((0, 0, -1), (-1, -1, -0.55), (1, 1, -0.55)):
            x2, y2 = cxc + dx * r * 1.15, cyc - r * 0.9 + dy * r * 0.75
            x1, y1 = cxc + dx * r * 0.75, cyc - r * 0.6 + dy * r * 0.35
            dr.line([x1, y1, x2, y2], fill=ASA_C, width=2)
    elif band == "hiru":   # まんまるの陽＋光線8本
        cxc, cyc = x + s / 2, y + s / 2
        r = s * 0.26
        dr.ellipse([cxc - r, cyc - r, cxc + r, cyc + r], fill=HIRU_C)
        import math
        for i in range(8):
            a = math.pi / 4 * i
            x1, y1 = cxc + math.cos(a) * r * 1.5, cyc + math.sin(a) * r * 1.5
            x2, y2 = cxc + math.cos(a) * r * 2.0, cyc + math.sin(a) * r * 2.0
            dr.line([x1, y1, x2, y2], fill=HIRU_C, width=2)
    elif band == "yoru":   # 三日月（円からチップ地の色で欠けを作る）
        dr.ellipse([x + 1, y + 1, x + s - 1, y + s - 1], fill=YORU_C)
        off = s * 0.30
        dr.ellipse([x + 1 + off, y + 1 - off * 0.6, x + s - 1 + off, y + s - 1 - off * 0.6], fill=bg)

def tint(hexc, alpha=0.13):
    """カード地に色を薄く敷く（#rrggbb→BGに向けて混色）"""
    r, g, b = int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16)
    br, bg_, bb = 255, 255, 255
    f = lambda a, b_: int(a * alpha + b_ * (1 - alpha))
    return (f(r, br), f(g, bg_), f(b, bb))

EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]")

def font(size, bold=False):
    cands = ([r"C:\Windows\Fonts\YuGothB.ttc", r"C:\Windows\Fonts\meiryob.ttc"] if bold else
             [r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc"])
    cands += ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]
    for p in cands:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)

def ellipsis(draw, text, f, maxw):
    text = EMOJI.sub("", text).strip()
    if draw.textlength(text, font=f) <= maxw:
        return text
    while text and draw.textlength(text + "…", font=f) > maxw:
        text = text[:-1]
    return text + "…"

def render(data, year, month, out):
    sessions = data.get("sessions", [])
    # 日付→[(start, scenario, color, open)]
    bydate = {}
    for s in sessions:
        for d in s.get("dates", []):
            try:
                dt = datetime.date.fromisoformat(d["date"])
            except Exception:
                continue
            if dt.year == year and dt.month == month:
                bydate.setdefault(dt.day, []).append(
                    (d.get("start") or "", s.get("scenario") or "？", sys_color(s), s.get("open")))
    for v in bydate.values():
        v.sort(key=lambda x: x[0])
    suri = [s for s in sessions if s.get("suriawase")]

    # ---- レイアウト ----
    W = 1200; PAD = 28
    cell_w = (W - PAD * 2 - 6 * 6) // 7
    first = datetime.date(year, month, 1)
    ndays = (datetime.date(year + (month == 12), month % 12 + 1, 1) - first).days
    lead = first.weekday() == 6 and 0 or first.weekday() + 1  # 日曜始まり
    lead = (first.weekday() + 1) % 7
    weeks = -(-(lead + ndays) // 7)
    maxrows = max([len(v) for v in bydate.values()] + [1])
    line_h = 26
    cell_h = max(96, 30 + min(maxrows, 4) * line_h + 6)
    head_h = 92; dow_h = 30
    suri_h = (44 + 30 * len(suri)) if suri else 0
    foot_h = 54
    H = head_h + dow_h + weeks * (cell_h + 6) + suri_h + foot_h

    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    f_title = font(34, True); f_sub = font(17); f_dow = font(16, True)
    f_day = font(16, True); f_chip = font(15); f_small = font(15)

    # ヘッダ
    dr.text((PAD, 24), f"{year}年{month}月の卓予定", font=f_title, fill=GRN)
    right = f"{data.get('guild','')}　更新 {data.get('updated','')}"
    dr.text((W - PAD - dr.textlength(right, font=f_sub), 40), right, font=f_sub, fill=MUT)

    # 曜日
    dows = ["日", "月", "火", "水", "木", "金", "土"]
    y0 = head_h
    for i, w in enumerate(dows):
        x = PAD + i * (cell_w + 6)
        c = SUN if i == 0 else SAT if i == 6 else MUT
        dr.text((x + cell_w / 2 - dr.textlength(w, font=f_dow) / 2, y0), w, font=f_dow, fill=c)

    # セル
    today = datetime.date.today()
    y0 += dow_h
    for day in range(1, ndays + 1):
        idx = lead + day - 1
        r, c = divmod(idx, 7)
        x = PAD + c * (cell_w + 6); y = y0 + r * (cell_h + 6)
        is_today = (today.year, today.month, today.day) == (year, month, day)
        dr.rounded_rectangle([x, y, x + cell_w, y + cell_h], 9,
                             fill=CARD, outline=GRN if is_today else CLN, width=2 if is_today else 1)
        dc = SUN if c == 0 else SAT if c == 6 else MUT
        dr.text((x + 7, y + 4), str(day), font=f_day, fill=dc)
        chips = bydate.get(day, [])
        for i, (st, name, col, op) in enumerate(chips[:4]):
            cy = y + 28 + i * line_h
            if i == 3 and len(chips) > 4:
                dr.text((x + 8, cy + 2), f"…他{len(chips) - 3}卓", font=f_chip, fill=TER)
                break
            chip_bg = tint(col)
            dr.rounded_rectangle([x + 5, cy, x + cell_w - 5, cy + line_h - 4], 6, fill=chip_bg)
            dr.rectangle([x + 5, cy, x + 8, cy + line_h - 4], fill=col)
            bd = time_band(st)
            tx0 = x + 13
            if bd:
                draw_time_icon(dr, bd, x + 12, cy + 4, 14, chip_bg)
                tx0 = x + 30
            dr.text((tx0, cy + 2), ellipsis(dr, name, f_chip, x + cell_w - 5 - tx0 - 6), font=f_chip, fill=TX)

    # すり合わせ卓（日程未定）
    yy = y0 + weeks * (cell_h + 6) + 6
    if suri:
        dr.rectangle([PAD, yy + 5, PAD + 14, yy + 19], fill="#8a5a28")  # 貼り紙風の色角
        dr.text((PAD + 22, yy), "日程すり合わせ中", font=font(18, True), fill="#8a5a28")
        yy += 32
        for s in suri:
            col = sys_color(s)
            dr.rectangle([PAD + 2, yy + 3, PAD + 5, yy + 21], fill=col)
            meta = f"　GM: {s.get('gm') or '？'}" + ("　🟢募集中" if s.get("open") else "")
            dr.text((PAD + 12, yy), ellipsis(dr, (s.get("scenario") or "？") + meta, f_small, W - PAD * 2 - 20),
                    font=f_small, fill=TX)
            yy += 30
        yy += 6

    # フッタ
    n_open = sum(1 for s in sessions if s.get("open"))
    foot = f"掲載 {len(sessions)}卓（募集中 {n_open}）／詳細・最新はボードへ → namezu.github.io/henkyo-session-board"
    dr.text((PAD, yy + 12), foot, font=f_sub, fill=MUT)

    img.save(out)
    print(f"🖼 カレンダー画像: {out} ({W}x{H})")
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    today = datetime.date.today()
    ap.add_argument("-y", type=int, default=today.year)
    ap.add_argument("-m", type=int, default=today.month)
    ap.add_argument("-i", default=os.path.join(HERE, "sessions.json"))
    ap.add_argument("-o", default=os.path.join(HERE, "calendar.png"))
    a = ap.parse_args()
    with open(a.i, encoding="utf-8") as f:
        data = json.load(f)
    render(data, a.y, a.m, a.o)
