# -*- coding: utf-8 -*-
"""セッションカレンダー収集係（読み取り専用・チャルタヴォラのトークンで動く）
==================================================================
卓部屋フォーラムのトピックを読み、parse_title（実測99.1%）でタイトルを解析して
sessions.json を生成する。ボードはこのJSONを表示するだけ＝正本はDiscordのトピック。

・「掲載不要」がトピック本文(1投稿目)にあれば掲載しない（オプトアウト）
・タイトルが読めなかった卓は unparsed に載せる（ボードの⚠枠→GMがタイトルを直せば次回から載る）
・タグ「募集中」の有無で募集状態を判定
・卓情報に変化がなければ sessions.json を書き換えない（Actionsの無駄コミット防止＝updatedは「最終変化時刻」）
GitHub Actions(15分毎)では DISCORD_BOT_TOKEN を Secret から環境変数で渡す（.env不要）。
"""
import os, json, asyncio, datetime, re
import discord
from dotenv import load_dotenv
from parse_title import parse

HERE = os.path.dirname(os.path.abspath(__file__))
# トークン＝チャルタヴォラ（読み取り専用bot）を流用。ローカルは.env、Actionsは環境変数（.envが無ければ素通り）
_envfile = os.environ.get("COLLECT_ENV", os.path.join(HERE, "..", "chartavora_bot", ".env"))
if os.path.exists(_envfile):
    load_dotenv(_envfile)
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = int(os.environ.get("BOARD_GUILD_ID", "1023884809774305301"))       # 既定=LODGE
FORUM_NAMES = [x.strip() for x in os.environ.get("BOARD_FORUMS", "🏡卓部屋一覧-test").split(",") if x.strip()]
OUT = os.environ.get("BOARD_OUT", os.path.join(HERE, "sessions.json"))
OPTOUT = "掲載不要"

def infer_year(month, day, today):
    """月/日だけのタイトルに年を割り当てる（60日以上過去なら来年扱い）"""
    y = today.year
    try:
        d = datetime.date(y, month, day)
    except ValueError:
        return None
    if (today - d).days > 60:
        return y + 1
    return y

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

async def read_forum(guild, key, sessions, unparsed):
    # key＝フォーラム名 or チャンネルID（数字）。IDのほうが絵文字/改名に強く確実
    if key.isdigit():
        forum = guild.get_channel(int(key))
        if not isinstance(forum, discord.ForumChannel):
            forum = None
    else:
        forum = discord.utils.get(guild.forums, name=key)
    if forum is None:
        print(f"⚠ フォーラム「{key}」が見つからない/見えない"); return
    ths = list(forum.threads)
    try:
        async for th in forum.archived_threads(limit=None):
            ths.append(th)
    except Exception as e:
        print(f"⚠ {name}: アーカイブ取得で一部失敗({e})")
    today = datetime.date.today()
    for th in ths:
        url = f"https://discord.com/channels/{guild.id}/{th.id}"
        tags = {t.name for t in th.applied_tags}
        # オプトアウト＝タグ「掲載不要」（推奨・Message Content権限不要）or 1投稿目本文（旧方式・当面併読）
        try:
            starter = await th.fetch_message(th.id)
        except Exception:
            starter = None
        if OPTOUT in tags or (starter and OPTOUT in (starter.content or "")):
            print(f"  ⏭ 掲載不要: {th.name}"); continue
        gm0 = starter.author.display_name if (starter and starter.author) else None
        r = parse(th.name)
        if not r["ok"]:
            # 読めなくても分かる情報（GM名・募集状態）は⚠貼り紙に添える
            unparsed.append({"title": th.name, "url": url, "gm": gm0,
                             "open": "募集中" in tags})
            print(f"  ⚠ 読めない→⚠枠: {th.name}"); continue
        dates = []
        for (m, d) in r["dates"]:
            y = infer_year(m, d, today)
            if y:
                dates.append({"date": f"{y}-{m:02d}-{d:02d}", "start": r["start"], "end": r["end"]})
        gm = None
        if starter and starter.author:
            gm = starter.author.display_name
        sessions.append({
            "scenario": r["scenario"],
            "reg": None if r.get("reg_is_name") else r["reg"],
            "reg_is_name": bool(r.get("reg_is_name")),
            "dates": dates,
            "open": "募集中" in tags,
            "suriawase": bool(r["suriawase"] and not dates),
            "gm": gm, "url": url,
            "created": th.created_at.isoformat() if th.created_at else None,
            "source": "forum",
        })
        print(f"  ✅ {th.name}")

@client.event
async def on_ready():
    try:
        guild = client.get_guild(GUILD_ID)
        if guild is None:
            print(f"⚠ サーバー {GUILD_ID} が見えない"); return
        sessions, unparsed = [], []
        for name in FORUM_NAMES:
            await read_forum(guild, name, sessions, unparsed)
        data = {
            "updated": datetime.datetime.now().strftime("%Y/%m/%d %H:%M"),
            "guild": guild.name,
            "sessions": sessions,
            "unparsed": unparsed,
        }
        # 変化なしなら書き換えない（updatedの時刻差だけで毎回コミットしないため）
        if os.path.exists(OUT):
            try:
                with open(OUT, encoding="utf-8") as f:
                    old = json.load(f)
                if {k: v for k, v in old.items() if k != "updated"} == \
                   {k: v for k, v in data.items() if k != "updated"}:
                    print(f"📋 変化なし: 掲載{len(sessions)}件／⚠{len(unparsed)}件（sessions.jsonは据え置き）")
                    return
            except Exception:
                pass
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"📋 sessions.json 書き出し: 掲載{len(sessions)}件／⚠{len(unparsed)}件 → {OUT}")
    finally:
        await client.close()

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("チャルタヴォラのトークンが見つからない（chartavora_bot/.env）")
    client.run(TOKEN)
