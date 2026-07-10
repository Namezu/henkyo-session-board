# -*- coding: utf-8 -*-
"""村の月次データレポート係（毎月1日に前月分を運営chへ投稿）
=================================================================
sessions.json（チャルタヴォラ収集・全履歴）から前月の供給統計を集計し、
Discord webhook（REPORT_WEBHOOK_URL）へテキスト投稿する。

⚠ GM名入りの集計を含む＝**運営限定チャンネル（円卓会議/領民議会等）のwebhookのみ**に設定すること。
  REPORT_WEBHOOK_URL 未設定のときは標準出力に印字して正常終了（=配線するまで何もしない安全設計）。

環境変数: REPORT_WEBHOOK_URL（運営ch webhook）／BOARD_SESSIONS（既定 sessions.json）
起動: python collect/monthly_report.py [YYYY-MM]（引数省略=前月）
由来: 村データ分析v1（2026-07-10・reports/村データ分析_v1_2026-07-10.md）の定点観測化。
"""
import os, sys, json, re, datetime, collections, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.environ.get("BOARD_SESSIONS") or os.path.join(os.path.dirname(HERE), "sessions.json")
if not os.path.exists(SESSIONS):
    SESSIONS = os.path.join(HERE, "sessions.json")
WEBHOOK = os.environ.get("REPORT_WEBHOOK_URL", "").strip()

# ---- システム分類（index.htmlのsysGroupと揃える＝レギュ追加時は両方更新） ----
AR2E_REGS = ["ミスリルクレスト","ミスリルクエスト","氷原開拓団","さちあれ","フツウノアリアン","イジョウナアリアン","ピースメイカー","アンコールを流星に","賽は投げられた","ダンジョン・トラベラーズ","ネームドエネミー討伐RTA!!","勇者の首を晒せ","グルメ卓"]
REG2SYS = {"この素晴らしいエリンにフェイトを！":"その他","アリアンストラテジー":"その他","JAIL HOUSE":"サタスペ","カルティックサタデーナイトスペシャル":"サタスペ","スクランブルサタデーナイトスペシャル":"サタスペ","デッドマン・ウォーキング":"シノビガミ","辺境村スタンダード":"SW2.5","ドロップアウト・アーカイブ":"ブルアカ","アオハルライフ":"ブルアカ"}
SYS_DEFS = [("アリアンロッド2E", re.compile(r"アリアンロッド|AR2E", re.I)), ("SW2.5", re.compile(r"SW2\.?5|ソードワールド", re.I)),
            ("CoC", re.compile(r"CoC|クトゥルフ|狂気山脈|[67]版", re.I)), ("サタスペ", re.compile(r"サタスペ")),
            ("シノビガミ", re.compile(r"シノビガミ")), ("DX3rd", re.compile(r"DX3rd|ダブルクロス", re.I)),
            ("ブルアカ", re.compile(r"ブルアカ")), ("ステラナイツ", re.compile(r"ステラナイツ")),
            ("D&D", re.compile(r"D&D|DnD|ダンジョンズ", re.I)), ("アークナイツ", re.compile(r"アークナイツ"))]

def _norm(t):
    return re.sub(r"[\s　]+", "", t or "").lower()

def sys_of(s):
    n = _norm(s.get("reg"))
    if n:
        for k in AR2E_REGS:
            nk = _norm(k)
            if n == nk or nk in n:
                return "アリアンロッド2E"
        for k, v in REG2SYS.items():
            nk = _norm(k)
            if n == nk or nk in n:
                return v
    probe = (s.get("reg") or "") + " " + (s.get("scenario") or "")
    for name, rx in SYS_DEFS:
        if rx.search(probe):
            return name
    return "その他"

def reg_canon(s):
    n = _norm(s.get("reg"))
    for k in AR2E_REGS:
        if n and (n == _norm(k) or _norm(k) in n):
            return k
    return s.get("reg") or "（レギュなし）"

def first_date(s):
    ds = sorted(x["date"] for x in (s.get("dates") or []) if x.get("date"))
    if ds:
        return ds[0]
    c = s.get("created")
    return c[:10] if c else None

def band(st):
    if not st:
        return "未定"
    try:
        h = int(str(st).split(":")[0])
    except Exception:
        return "未定"
    return "昼" if 6 <= h < 16 else "夕" if 16 <= h < 20 else "夜"

def build_report(sessions, ym):
    """ym='2026-06' の月次レポート本文（Discordテキスト）を作る純関数。"""
    y, m = map(int, ym.split("-"))
    prev_ym = f"{y-1}-12" if m == 1 else f"{y}-{m-1:02d}"
    lastyear_ym = f"{y-1}-{m:02d}"
    # 卓単位（初開催日がその月）
    def in_month(target):
        out = []
        for s in sessions:
            fd = first_date(s)
            if fd and fd[:7] == target:
                out.append(s)
        return out
    cur, prv, lyr = in_month(ym), in_month(prev_ym), in_month(lastyear_ym)
    n = len(cur)
    # 開催日単位の時間帯（当月の全dates展開）
    bands = collections.Counter()
    for s in sessions:
        for x in (s.get("dates") or []):
            if (x.get("date") or "")[:7] == ym:
                bands[band(x.get("start"))] += 1
    # GM
    gms = collections.Counter((s.get("gm") or "?") for s in cur)
    top3 = gms.most_common(3)
    top3_share = round(sum(c for _, c in top3) * 100 / n) if n else 0
    # 新GMデビュー（全履歴で初卓がこの月）
    first_gm = {}
    for s in sorted(sessions, key=lambda x: first_date(x) or "9999"):
        fd = first_date(s)
        g = s.get("gm")
        if fd and g and g not in first_gm:
            first_gm[g] = fd
    debuts = [g for g, d in first_gm.items() if d[:7] == ym]
    # システム/レギュ
    sysc = collections.Counter(sys_of(s) for s in cur)
    regc = collections.Counter(reg_canon(s) for s in cur if sys_of(s) == "アリアンロッド2E")
    beginner = sum(1 for s in cur if re.search(r"初心者|歓迎", s.get("scenario") or ""))
    def diff(a, b):
        if not b:
            return "—"
        d = a - b
        return f"{'+' if d >= 0 else ''}{d}"
    lines = [
        f"📊 **辺境TRPG村 月次レポート {ym}**（🔒運営内部用・チャルタヴォラ収集データ）",
        "",
        f"**卓数: {n}卓**（前月{len(prv)}卓 {diff(n, len(prv))}／前年同月{len(lyr)}卓 {diff(n, len(lyr))}）",
        f"時間帯（開催日単位）: 夜{bands['夜']}・昼{bands['昼']}・夕{bands['夕']}・時刻未定{bands['未定']}",
        f"システム: " + " ".join(f"{k}{v}" for k, v in sysc.most_common(4)),
        f"AR2Eレギュ上位: " + " ".join(f"{k}{v}" for k, v in regc.most_common(5)),
        "",
        f"**GM: {len(gms)}人**（上位3人で{top3_share}%＝" + "・".join(f"{g}{c}卓" for g, c in top3) + "）",
        f"🌱 初卓デビューGM: {len(debuts)}人" + (f"（{'・'.join(debuts)}）" if debuts else "（0人＝声かけの好機かも）"),
        f"🔰 初心者歓迎を明示した卓: {beginner}卓",
        "",
        "-# スカラバエウスの月次観測。数字の解釈や施策の相談は執政官まで。",
    ]
    return "\n".join(lines)

def post(text):
    body = json.dumps({"content": text[:1990], "username": "スカラバエウス（月次観測）",
                       "allowed_mentions": {"parse": []}}).encode("utf-8")
    req = urllib.request.Request(WEBHOOK, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status

if __name__ == "__main__":
    data = json.load(open(SESSIONS, encoding="utf-8"))
    if len(sys.argv) > 1:
        ym = sys.argv[1]
    else:
        today = datetime.date.today()
        last = today.replace(day=1) - datetime.timedelta(days=1)
        ym = f"{last.year}-{last.month:02d}"
    report = build_report(data.get("sessions") or [], ym)
    print(report)
    if WEBHOOK:
        st = post(report)
        print(f"→ webhook投稿: HTTP {st}")
    else:
        print("→ REPORT_WEBHOOK_URL未設定＝印字のみ（配線するまで投稿しない）")
