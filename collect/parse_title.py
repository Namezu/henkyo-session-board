# -*- coding: utf-8 -*-
"""卓部屋トピックタイトルのパーサー（表記ゆれ耐性の要）
対応: `7/12 21:00-25:00【レギュ】名` / `~`/`～`/`-`混在 / `(木)`曜日 / 複数日`7/11,12,14` /
     `日程すり合わせ【..】` `卓ピタ調整【..】` / `【第一話】7/11【SW2.5】名`(話数枠を除外) /
     `【レギュ】名【高難易度】`(複数【】＝台帳照合でレギュを選び他はメタ扱い) /
     レギュ名の表記ゆれ（誤記/装飾/かな/別名）を台帳へあいまいスナップ
実測: chartavora採集の実タイトル1,186本でテスト（__main__）。
"""
import re, json, sys, unicodedata, difflib

EP = re.compile(r"^(第?[0-9０-９一二三四五六七八九十]+話|最終話|前編|後編|中編|完結編|序章|終章)$")
SURI = re.compile(r"すり合わせ|すりあわせ|摺り合わせ|擦り合わせ|日程調整|卓ピタ調整|卓ピタ|日程相談|スケジュール調整|調整中|延期|仮置き|いずれか|[XxＸｘ]{2}")
TIME = re.compile(r"([0-9０-９]{1,2})[:：]([0-9０-９]{2})")
TIME_KANJI = re.compile(r"([0-9０-９]{1,2})\s*時(?:\s*([0-9０-９]{1,2})\s*分)?(?:半)?")
DATE = re.compile(r"([0-9０-９]{1,2})\s*[/／]\s*([0-9０-９]{1,2})")
DATE_KANJI = re.compile(r"([0-9０-９]{1,2})\s*月\s*([0-9０-９]{1,2})\s*日")

# ── レギュ台帳（表記ゆれ吸収用）──────────────────────────────
# 正本はチャルタヴォラ採集のレギュ一覧＋index.htmlのAR2E_REGS/REG2SYS。当面はここにも置く
# （レギュ追加時は index.html と calendar_image.py と ここ の3箇所を更新／将来JSON一本化=Cで解消）
REG_CANON = [
    # AR2Eレギュ
    "ミスリルクレスト", "ミスリルクエスト", "氷原開拓団", "さちあれ", "フツウノアリアン",
    "イジョウナアリアン", "ピースメイカー", "アンコールを流星に", "賽は投げられた",
    "ダンジョン・トラベラーズ", "ネームドエネミー討伐RTA!!", "勇者の首を晒せ", "グルメ卓",
    # 他システムのレギュ（チャルタヴォラ採集）
    "この素晴らしいエリンにフェイトを！", "アリアンストラテジー", "JAIL HOUSE",
    "カルティックサタデーナイトスペシャル", "スクランブルサタデーナイトスペシャル",
    "デッドマン・ウォーキング", "辺境村スタンダード", "ドロップアウト・アーカイブ", "アオハルライフ",
    # システム名そのものがレギュ欄に来る形（【SW2.5】【アリアンロッド2E】等）
    "アリアンロッド2E", "SW2.5", "シノビガミ", "サタスペ", "DX3rd", "ブルアカ", "ステラナイツ",
]
# 明示的別名（あいまい照合で拾いきれない同義語・略記）。左→右(正本)。増やしてよい
REG_ALIASES = {
    "AR2E": "アリアンロッド2E", "アリアンロッド": "アリアンロッド2E",
    "ソードワールド2.5": "SW2.5", "SW25": "SW2.5",
}
# メタ枠（難易度/対象など＝レギュでない飾り）＝tentativeレギュ選定で飛ばす
META = re.compile(r"^((たぶん)?(低|高)レベル|高難[易度]度?|低難[易度]度?|難易度.*|初心者.*|初見.*|経験者.*|見学.*|募集.*|満[員卓]|〆?切.*|テスト.*|練習.*)$")

_KANA = str.maketrans({chr(c): chr(c + 0x60) for c in range(0x3041, 0x3097)})  # ひらがな→カタカナ
_DECO = re.compile(r"[\s　・･‐\-−ー―~〜∼、。.,／/｜|【】『』（）()「」☆★♪✦→…！!?？]+")

def _norm(s):
    """あいまい照合用の強い正規化：NFKC→かな統一→装飾/長音/記号除去→小文字"""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.translate(_KANA)
    s = _DECO.sub("", s)
    return s.lower()

_REG_NORM = [(r, _norm(r)) for r in REG_CANON]

def canon_reg(text):
    """ブラケット/文字列を台帳の正規レギュ名へスナップ。当たらなければ None。"""
    if not text:
        return None
    n = _norm(text)
    if not n:
        return None
    for a, canon in REG_ALIASES.items():          # ① 別名表
        if n == _norm(a):
            return canon
    for r, rn in _REG_NORM:                        # ② 正規化して完全一致
        if n == rn:
            return r
    for r, rn in _REG_NORM:                        # ③ 相互包含（接尾辞CL1等・装飾付き）
        if len(rn) >= 3 and (rn in n or n in rn):
            return r
    best, bestr = 0.0, None                        # ④ あいまい（編集距離ベース）
    for r, rn in _REG_NORM:
        if len(rn) < 3:
            continue
        ratio = difflib.SequenceMatcher(None, n, rn).ratio()
        if ratio > best:
            best, bestr = ratio, r
    return bestr if best >= 0.84 else None

def z2h(s):
    return s.translate(str.maketrans("０１２３４５６７８９：／", "0123456789:/"))

def parse(title):
    t = z2h(title.strip())
    out = {"raw": title, "reg": None, "scenario": None, "dates": [], "start": None, "end": None,
           "suriawase": False, "episode": None, "ok": False}
    # 年プレフィックス（2024/4/8等）を除去してから日付を読む。年が明記されていたか記録（継続CP判定で使う）
    out["year_explicit"] = bool(re.search(r"20[0-9]{2}\s*[/年]", t))
    t = re.sub(r"20[0-9]{2}\s*[/年]\s*", "", t)
    # 【】群（無ければ『』をタイトル括弧として代用）を分類：話数/レギュ(台帳照合)/メタ
    braces = [(m.start(), m.end(), m.group(1).strip()) for m in re.finditer(r"【([^】]*)】", t)]
    quote_style = False  # 『』派＝レギュ枠でなく作品タイトル括弧（狂気山脈カテナ班の実例 2026-07-05）
    if not braces:
        braces = [(m.start(), m.end(), m.group(1).strip()) for m in re.finditer(r"『([^』]*)』", t)]
        quote_style = bool(braces)
    for s_, e_, inner in braces:            # 話数枠はどちらの派でも先に抜く
        if EP.match(inner):
            out["episode"] = inner
    # レギュ枠の選定（『』派はレギュでなく作品タイトル＝reg_idxは立てない）
    reg = None; reg_idx = None
    if not quote_style:
        # ① 台帳にスナップする最初の【】をレギュに（複数【】でも本物のレギュを選ぶ＝五等分バグの根治）
        for i, (s_, e_, inner) in enumerate(braces):
            if EP.match(inner):
                continue
            c = canon_reg(inner)
            if c:
                reg, reg_idx = c, i
                break
        # ② 当たらなければ、話数でもメタでもない最初の【】を暫定レギュに
        if reg_idx is None:
            for i, (s_, e_, inner) in enumerate(braces):
                if EP.match(inner) or META.match(inner):
                    continue
                reg, reg_idx = (inner or None), i
                break
        out["reg"] = reg
    # 括弧の外の地の文（日付/時刻/すり合わせ判定用）＝『』の中身は残す（作品タイトルだから）
    outside = re.sub(r"『([^』]*)』", r" \1 ", t)
    outside = re.sub(r"【[^】]*】", " ", outside)
    # 日付（外の地の文から。7/11,12,14 の日only継続にも対応。漢字日付「2月4日」も）
    dates = []
    m0 = DATE.search(outside) or DATE_KANJI.search(outside)
    if m0:
        month = int(m0.group(1)); day = int(m0.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            dates.append((month, day))
            rest = outside[m0.end():]
            for cm in re.finditer(r"[,、・]\s*([0-9]{1,2})(?:/([0-9]{1,2}))?", rest):
                if cm.group(2):
                    mm, dd = int(cm.group(1)), int(cm.group(2))
                else:
                    mm, dd = month, int(cm.group(1))
                if 1 <= mm <= 12 and 1 <= dd <= 31:
                    dates.append((mm, dd))
                rest_upto = rest[:cm.start()]
                if ":" in rest_upto:
                    dates = dates[:1]
                    break
    out["dates"] = dates
    # すり合わせ型
    if SURI.search(outside) or (not dates and SURI.search(t)):
        out["suriawase"] = True
    # 時刻（外の地の文から。開始〜終了。「21時」「21時30分」表記も）
    times = TIME.findall(outside)
    if not times:
        times = [(h, mm or "00") for h, mm in TIME_KANJI.findall(outside)]
    if times:
        out["start"] = f"{int(times[0][0])}:{times[0][1]}"
        if len(times) >= 2:
            out["end"] = f"{int(times[1][0])}:{times[1][1]}"
    # シナリオ名の素材＝レギュ【】の直後〜次の【】まで（括弧前の日時/装飾ゴミを拾わない）。
    #   レギュ枠が無い（『』派/【】無し/メタだけ）時は括弧の外の地の文全体を使う
    if reg_idx is not None:
        seg_start = braces[reg_idx][1]
        seg_end = len(t)
        for (s2, e2, in2) in braces:
            if s2 >= seg_start:
                seg_end = s2; break
        seg = t[seg_start:seg_end]
    else:
        seg = outside
    seg = re.sub(r"『([^』]*)』", r" \1 ", seg)   # 『作品名』→中身をシナリオに（複数併記は空白区切り）
    s = DATE.sub(" ", seg); s = DATE_KANJI.sub(" ", s); s = TIME.sub(" ", s)
    s = re.sub(r"[0-9]{1,2}\s*時(?:\s*[0-9]{1,2}\s*分)?(?:半)?", " ", s)
    s = re.sub(r"[（(][月火水木金土日][）)]", " ", s)
    s = re.sub(r"[,、・]\s*[0-9]{1,2}(?:/[0-9]{1,2})?", " ", s)
    s = re.sub(r"^[\s.．\-~〜～‐:：/／。、，・0-9]+", "", s)
    s = re.sub(r"[\s.．\-~〜～‐]+$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    if len(s) >= 2:
        out["scenario"] = s
    elif reg and not quote_style:
        # 【レギュ】のみ or シナリオが取れない＝レギュ名が実質タイトル（シリーズ卓に多い実例）
        out["scenario"] = reg
        out["reg_is_name"] = True
    elif s:
        out["scenario"] = s
    # 成立判定：名前が取れて、（日付あり）or（すり合わせ）。開始時刻は無くても掲載可（時刻未定表示）
    out["ok"] = bool(out["scenario"] and (out["dates"] or out["suriawase"]))
    return out

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "../chartavora_bot/_titles_ar2e.json"
    titles = json.load(open(src, encoding="utf-8"))
    ok, partial, fail = [], [], []
    for t in titles:
        r = parse(t)
        if r["ok"]:
            ok.append(r)
        elif r["reg"] and r["scenario"]:
            partial.append(r)   # レギュ・名は取れたが日時が不明（挙動：すり合わせ扱い or 手動枠）
        else:
            fail.append(r)
    n = len(titles)
    print(f"対象: {n}本")
    print(f"完全認識(日時/すり合わせまで判定OK): {len(ok)}本 ({len(ok)/n*100:.1f}%)")
    print(f"部分認識(レギュ+名のみ・日時不明):   {len(partial)}本 ({len(partial)/n*100:.1f}%)")
    print(f"認識不能:                            {len(fail)}本 ({len(fail)/n*100:.1f}%)")
    print("\n--- 部分認識の例(最大10) ---")
    for r in partial[:10]: print("  ", r["raw"])
    print("\n--- 認識不能の例(最大15) ---")
    for r in fail[:15]: print("  ", r["raw"])
