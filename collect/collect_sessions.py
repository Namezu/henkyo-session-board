# -*- coding: utf-8 -*-
"""セッションカレンダー収集係（読み取り専用・チャルタヴォラのトークンで動く）
==================================================================
卓部屋フォーラムのトピックを読み、parse_title でタイトルを解析して sessions.json を生成する。
ボードはこのJSONを表示するだけ＝正本はDiscordのトピック。

・「掲載不要」がトピック本文(1投稿目)にあれば掲載しない（オプトアウト）
・タイトルが読めなかった卓は unparsed に載せる（ボードの⚠枠→GMがタイトルを直せば次回から載る）
  ただし「アーカイブ済み＆非募集中＝終了した卓」は読めなくても不掲載（終了卓の⚠枠居座り解消 2026-07-08）
・タグ「募集中」の有無で募集状態を判定

【省エネ収集（2026-07-08）】
・通常モード＝アクティブ卓＋作成が新しい卓(CUTOFF_DAYS以内)だけ実フェッチ。古い卓は前回JSONから再利用
  （＝毎回1300件を fetch_message/fetch_member するのをやめ、変わらない過去卓は据え置く。過去の月/年表示は維持）
・フルモード（環境変数 COLLECT_FULL=1・日次/手動）＝全履歴を実フェッチしてドリフト（過去卓の編集/退会/オプトアウト）を修復
・updated=掲載内容の最終変化時刻／checked=最終チェック時刻（数時間おきハートビートで「動いてる感」を出す＝コミット過多回避）

GitHub Actions では DISCORD_BOT_TOKEN を Secret から環境変数で渡す（.env不要）。
"""
import os, json, datetime, re
import discord
from dotenv import load_dotenv
from parse_title import parse

HERE = os.path.dirname(os.path.abspath(__file__))
JST = datetime.timezone(datetime.timedelta(hours=9))   # GitHub ActionsはUTC＝時刻/日付は日本時間で記録・判定
LIMIN_ROLE_ID = int(os.environ.get("LIMIN_ROLE_ID", "1001840163724464270"))  # 辺境TRPG村「領民」ロール＝卓を立てられる正式メンバーの証（退会/剥奪は持たない・ぱんさー例2026-07-07）
_envfile = os.environ.get("COLLECT_ENV", os.path.join(HERE, "..", "chartavora_bot", ".env"))
if os.path.exists(_envfile):
    load_dotenv(_envfile)
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = int(os.environ.get("BOARD_GUILD_ID", "1023884809774305301"))       # 既定=LODGE
FORUM_NAMES = [x.strip() for x in os.environ.get("BOARD_FORUMS", "🏡卓部屋一覧-test").split(",") if x.strip()]
OUT = os.environ.get("BOARD_OUT", os.path.join(HERE, "sessions.json"))
OPTOUT = "掲載不要"
STALE_DAYS = 180                                                              # 半年以上前に立てたトピックは流卓/終了とみなす
CUTOFF_DAYS = int(os.environ.get("COLLECT_CUTOFF_DAYS", "150"))               # 通常モード＝これより新しい作成日の卓だけ実フェッチ（古い卓は前回JSON再利用）
FULL_MODE = os.environ.get("COLLECT_FULL", "").strip() == "1"                 # 全履歴を実フェッチ（日次/手動でドリフト修復）
HEARTBEAT_HOURS = int(os.environ.get("COLLECT_HEARTBEAT_HOURS", "3"))         # 変化なしでもこの間隔でcheckedを更新（動いてる感・コミットは数時間に1回）


def infer_year(month, day, base):
    """月/日だけのタイトルに年を割り当てる。基準＝トピック作成日(base)。
    卓は作成後に開催されるので作成年が基本。卓日が作成日より前なら年をまたいだ翌年開催。"""
    y = base.year
    try:
        d = datetime.date(y, month, day)
    except ValueError:
        return None
    if d < base:
        y += 1
        try:
            datetime.date(y, month, day)
        except ValueError:
            return None
    return y


def parse_iso_date_jst(s):
    """ISO文字列(created)→JST日付。読めなければNone。（純関数＝テスト可能）"""
    if not s:
        return None
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(JST)
        return dt.date()
    except Exception:
        return None


# CP名らしさ＝「CP」「キャンペーン」だが直後がひらがな(＝動詞句『キャンペーンやる』等)は除外／第n話は継続もの。
# 例: 「一期一会CP」「○○キャンペーン」「キャンペーンⅡ」=名前→○ ／「キャンペーンやる」「CPやる」=動詞句→×
_CP_RE = re.compile(r"(?:CP|キャンペーン)(?![ぁ-ゟ])|第[0-9０-９一二三四五六七八九十]+話")
def is_ongoing_cp(title, scenario, all_dates_past, year_explicit,
                  created_date, last_active_date, today,
                  created_min_age=90, active_within=45):
    """「タイトルが初回日付のまま進行中のCP」を検出（純関数＝テスト可能）。
    去年の"死んだ"卓を今年に湧かせないため、次の全条件を満たす時だけ継続中とみなす：
    ・パース済みの日付が全部過去（＝タイトルが古い）／年が明記されていない
    ・トピック作成が十分古い（created_min_age日以上前＝最近の単発ではない）
    ・最終活動が最近（active_within日以内＝まだ生きている。死んだ卓は無活動で除外）
    ・タイトル/シナリオがCP・キャンペーン・第n話等の"継続もの"らしい"""
    if not all_dates_past or year_explicit:
        return False
    if created_date is None or (today - created_date).days < created_min_age:
        return False
    if last_active_date is None or (today - last_active_date).days > active_within:
        return False
    return bool(_CP_RE.search((title or "") + " " + (scenario or "")))


def merge_old_sessions(fresh_sessions, processed_urls, old_sessions, cutoff_date):
    """今回フェッチしなかった古い卓（作成がcutoffより前）を前回JSONから引き継ぐ。
    ＝省エネしつつ過去の月/年表示を維持する。processed_urls に居る卓（今回実フェッチ済み）は
    freshが正なので引き継がない＝削除/オプトアウト/編集が正しく反映される。（純関数＝テスト可能）"""
    kept = list(fresh_sessions)
    for s in (old_sessions or []):
        if s.get("url") in processed_urls:
            continue                       # 今回処理した＝freshが最新
        cdate = parse_iso_date_jst(s.get("created"))
        if cdate is not None and cdate < cutoff_date:
            kept.append(s)                 # 古い卓＝不変とみなして再利用
    return kept


def heartbeat_should_write(old, data, now_str, hb_hours):
    """書き込むべきか＋書き出すupdated/checkedを決める純関数。
    返り値: (write:bool, updated:str, checked:str)
    ・内容が変わった→書く（updated=now, checked=now）
    ・内容同じでも最終checkedからhb_hours経過→書く（updated据え置き, checked=now＝ハートビート）
    ・それ以外→書かない"""
    def content_key(d):
        return {k: v for k, v in (d or {}).items() if k not in ("updated", "checked")}
    changed = (old is None) or (content_key(old) != content_key(data))
    if changed:
        return True, now_str, now_str
    # 内容同じ＝ハートビート判定
    prev_checked = (old or {}).get("checked") or (old or {}).get("updated")
    due = True
    try:
        last = datetime.datetime.strptime(prev_checked, "%Y/%m/%d %H:%M")
        now = datetime.datetime.strptime(now_str, "%Y/%m/%d %H:%M")
        due = (now - last) >= datetime.timedelta(hours=hb_hours)
    except Exception:
        due = True
    if due:
        return True, (old or {}).get("updated", now_str), now_str   # updateは据え置き・checkedだけ更新
    return False, (old or {}).get("updated", now_str), prev_checked


_gm_active_cache = {}
async def gm_is_active(guild, author):
    """GM（起票者）が在籍かつ領民ロール保持か。退会/剥奪ならFalse。
    ⚠get_member（キャッシュ）でなく fetch_member（API直問い合わせ）で"今の在籍"を確認。
    一時エラーは在籍扱い＝現役GMの誤除外を防ぐ。同一GMは1回だけ問い合わせ。"""
    if author is None:
        return True
    aid = getattr(author, "id", None)
    if aid is None:
        return True
    if aid in _gm_active_cache:
        return _gm_active_cache[aid]
    try:
        m = await guild.fetch_member(aid)
        active = any(r.id == LIMIN_ROLE_ID for r in m.roles)
    except discord.NotFound:
        active = False
    except Exception:
        return True
    _gm_active_cache[aid] = active
    return active


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


async def read_forum(guild, key, sessions, unparsed, processed_urls, today):
    if key.isdigit():
        forum = guild.get_channel(int(key))
        if not isinstance(forum, discord.ForumChannel):
            forum = None
    else:
        forum = discord.utils.get(guild.forums, name=key)
    if forum is None:
        print(f"⚠ フォーラム「{key}」が見つからない/見えない"); return False
    cutoff_date = today - datetime.timedelta(days=CUTOFF_DAYS)
    ths = list(forum.threads)   # アクティブ卓は必ず処理（forum.threadsはプロパティ＝例外なし）
    archive_ok = True
    try:
        async for th in forum.archived_threads(limit=None):
            if not FULL_MODE:
                at = th.archive_timestamp
                # アーカイブは archive_timestamp 降順。cutoffより古くなったら以降は全部古い→打ち切り（省エネ）。
                # 作成日 <= アーカイブ日 なので、アーカイブがcutoffより古ければ作成もcutoffより古い＝再利用対象。
                if at and at.astimezone(JST).date() < cutoff_date:
                    break   # appendせず打ち切る（cutoff超の1件を後段に流さない）
            ths.append(th)
    except Exception as e:
        archive_ok = False
        print(f"⚠ {key}: アーカイブ取得が不完全({e})")
    if not archive_ok:
        # アーカイブ取得が途中失敗＝収集不完全→Falseを返して呼び出し側で書き込み中止（既存JSONを保護）。
        # 通常モードはbreakで短く済むので失敗は稀。フルモードは長いので失敗時こそ保護が効く。
        print(f"  🛡 {key}: 収集不完全のため既存を保護（書き込み見送り）")
        return False
    for th in ths:
        url = f"https://discord.com/channels/{guild.id}/{th.id}"
        cbase = th.created_at or getattr(th, "archive_timestamp", None)   # created_at欠損時はアーカイブ時刻で代替
        base_dt = cbase.astimezone(JST).date() if cbase else today
        if not FULL_MODE and base_dt < cutoff_date and bool(getattr(th, "archived", False)):
            continue   # 古い"アーカイブ済み"卓＝実フェッチせず前回JSONから引き継ぐ。古くてもアクティブな卓(継続CP/長期すり合わせ)は処理する
        processed_urls.add(url)
        tags = {t.name for t in th.applied_tags}
        recruiting = "募集中" in tags
        archived_done = bool(getattr(th, "archived", False)) and not recruiting   # アーカイブ済み＆非募集＝終了とみなす
        try:
            starter = await th.fetch_message(th.id)
        except Exception:
            starter = None
        if OPTOUT in tags or (starter and OPTOUT in (starter.content or "")) or (re.search(r"(?<!交)流卓|中止", th.name) is not None):
            print(f"  ⏭ 掲載不要/流卓: {th.name}"); continue
        gm0 = starter.author.display_name if (starter and starter.author) else None
        r = parse(th.name)
        if not r["ok"]:
            # 読めない卓＝⚠枠。ただし半年以上前 or 終了(アーカイブ済み＆非募集)は不掲載（居座り解消）
            if (today - base_dt).days > STALE_DAYS or archived_done:
                print(f"  🗑 終了/流卓とみなし不掲載(読めない): {th.name}"); continue
            unparsed.append({"title": th.name, "url": url, "gm": gm0, "open": recruiting})
            print(f"  ⚠ 読めない→⚠枠: {th.name}"); continue
        dates = []
        for (m, d) in r["dates"]:
            y = infer_year(m, d, base_dt)
            if y:
                dates.append({"date": f"{y}-{m:02d}-{d:02d}", "start": r["start"], "end": r["end"]})
        # 継続CP判定＝タイトルが初回(過去)日付のまま進行中の卓（例:takuさん一期一会CP）。
        # 去年の死んだ卓を今年に湧かせないため、最終活動が最近＆作成が古い＆CPらしい時だけ。
        today_iso = today.isoformat()
        all_past = bool(dates) and all(dd["date"] < today_iso for dd in dates)
        try:
            last_active = discord.utils.snowflake_time(th.last_message_id).astimezone(JST).date() if th.last_message_id else base_dt
        except Exception:
            last_active = base_dt
        ongoing = is_ongoing_cp(th.name, r["scenario"], all_past, r.get("year_explicit"),
                                base_dt, last_active, today)
        if ongoing:
            dates = []   # 古い初回日付は出さず「継続中」として日付なしで載せる（＝すり合わせ帯に表示）
        is_suri = bool((r["suriawase"] and not dates) or ongoing)
        # 半年以上前 or 終了(アーカイブ済み＆非募集)のすり合わせ卓＝居座るので不掲載（継続CPは除外＝残す）
        if is_suri and not ongoing and ((today - base_dt).days > STALE_DAYS or archived_done):
            print(f"  🗑 終了/流卓とみなし不掲載(すり合わせ): {th.name}"); continue
        gm = starter.author.display_name if (starter and starter.author) else None
        gm_active = await gm_is_active(guild, starter.author if starter else None)
        sessions.append({
            "scenario": r["scenario"],
            "reg": None if r.get("reg_is_name") else r["reg"],
            "reg_is_name": bool(r.get("reg_is_name")),
            "dates": dates,
            "open": recruiting,
            "suriawase": is_suri,
            "ongoing": ongoing,
            "gm": gm, "gm_active": gm_active, "url": url,
            "created": th.created_at.isoformat() if th.created_at else None,
            "source": "forum",
        })
        print(f"  {'🔄 継続CP' if ongoing else '✅'} {th.name}")
    return True


@client.event
async def on_ready():
    try:
        guild = client.get_guild(GUILD_ID)
        if guild is None:
            print(f"⚠ サーバー {GUILD_ID} が見えない"); return
        today = datetime.datetime.now(JST).date()
        sessions, unparsed, processed_urls = [], [], set()
        all_forums_ok = True
        for name in FORUM_NAMES:
            found = await read_forum(guild, name, sessions, unparsed, processed_urls, today)
            if not found:
                all_forums_ok = False
        # 既存を読む（マージ＋空上書き防止＋変化判定＋ハートビートで共用）
        old = None
        if os.path.exists(OUT):
            try:
                with open(OUT, encoding="utf-8") as f:
                    old = json.load(f)
            except Exception:
                old = None
        # 省エネ＝今回フェッチしなかった古い卓を前回JSONから引き継ぐ（フルモードは全部フェッチ済みなので実質何も足さない）
        if old and not FULL_MODE:
            cutoff_date = today - datetime.timedelta(days=CUTOFF_DAYS)
            before = len(sessions)
            sessions = merge_old_sessions(sessions, processed_urls, old.get("sessions"), cutoff_date)
            print(f"♻ 古い卓を前回JSONから再利用: +{len(sessions) - before}件（実フェッチ{before}件）")
        now_str = datetime.datetime.now(JST).strftime("%Y/%m/%d %H:%M")
        data = {"updated": now_str, "checked": now_str, "guild": guild.name,
                "sessions": sessions, "unparsed": unparsed}
        old_had = bool(old and (old.get("sessions") or old.get("unparsed")))
        # 🛡収集失敗の保険＝フォーラム未検出、または掲載も⚠枠も0件になった回は書き込まない（空上書き防止）
        if old_had and (not all_forums_ok or (not sessions and not unparsed)):
            reason = "フォーラム未検出" if not all_forums_ok else "掲載卓0件"
            print(f"🛡 収集が不完全({reason})＝sessions.jsonは据え置き（既存を保護：掲載{len(old.get('sessions', []))}件）")
            return
        write, upd, chk = heartbeat_should_write(old, data, now_str, HEARTBEAT_HOURS)
        if not write:
            print(f"📋 変化なし＆ハートビート未満: 掲載{len(sessions)}件／⚠{len(unparsed)}件（据え置き）")
            return
        data["updated"], data["checked"] = upd, chk
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        kind = "内容更新" if upd == now_str else "ハートビート"
        print(f"📋 sessions.json 書き出し[{kind}]: 掲載{len(sessions)}件／⚠{len(unparsed)}件 → {OUT}")
    finally:
        await client.close()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("チャルタヴォラのトークンが見つからない（chartavora_bot/.env）")
    client.run(TOKEN)
