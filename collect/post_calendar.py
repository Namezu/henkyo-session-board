# -*- coding: utf-8 -*-
"""カレンダー画像の日次webhook投稿係
=================================
sessions.json→calendar_image.pyでPNGを描き、Discord webhookに投稿する。
「古い投稿を自動削除→新しい画像を投稿」方式＝チャンネルには常に最新1枚だけ。
（メッセージIDは state ファイルに覚える。初回や消し損ねは黙って続行＝冪等）

環境: BOARD_WEBHOOK_URL（.env_board か環境変数）。円卓承認前は LODGE 地下室のwebhookのみ。
使い方: python post_calendar.py
"""
import os, json, datetime
import urllib.request, urllib.error
from dotenv import load_dotenv
from calendar_image import render, render_agenda

HERE = os.path.dirname(os.path.abspath(__file__))
_envfile = os.path.join(HERE, ".env_board")
if os.path.exists(_envfile):   # ローカルは.env_board、GitHub ActionsはSecretの環境変数
    load_dotenv(_envfile)
WEBHOOK = os.environ.get("BOARD_WEBHOOK_URL", "").strip()
SESS = os.environ.get("BOARD_SESSIONS", os.path.join(HERE, "sessions.json"))
STATE = os.environ.get("BOARD_STATE", os.path.join(HERE, ".calendar_msg_state.json"))

def api(url, method="GET", data=None, headers=None):
    h = {"User-Agent": "polistes-board (https://github.com/Namezu/henkyo-session-board, 1.0)"}
    h.update(headers or {})
    req = urllib.request.Request(url, method=method, data=data, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            return r.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return e.code, {}

def main():
    if not WEBHOOK:
        raise SystemExit("BOARD_WEBHOOK_URL がない（takuboard/.env_board）")
    today = datetime.date.today()
    with open(SESS, encoding="utf-8") as f:
        data = json.load(f)
    png = render(data, today.year, today.month, os.path.join(HERE, "calendar.png"))
    png2 = render_agenda(data, today.year, today.month, os.path.join(HERE, "agenda.png"))

    # 前回の投稿を削除（常に最新1枚だけ残す）
    if os.path.exists(STATE):
        try:
            old = json.load(open(STATE, encoding="utf-8"))
            st, _ = api(f"{WEBHOOK}/messages/{old['id']}", "DELETE")
            print(f"🗑 前回の投稿削除: status={st}")
        except Exception as e:
            print(f"⚠ 前回削除スキップ({e})")

    # multipart/form-data で画像＋一言を投稿
    boundary = "----polistes-board-boundary"
    content = (
        f"📅 **{today.month}月の卓予定**（毎日この投稿だけ更新・詳細はボードへ）\n"
        f"https://namezu.github.io/henkyo-session-board/"
    )
    payload = {"content": content, "attachments": [{"id": 0, "filename": "calendar.png"},
                                                   {"id": 1, "filename": "agenda.png"}]}
    body = b""
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
             f"Content-Type: application/json\r\n\r\n{json.dumps(payload, ensure_ascii=False)}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"calendar.png\"\r\n"
             f"Content-Type: image/png\r\n\r\n").encode()
    body += open(png, "rb").read()
    body += (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"files[1]\"; filename=\"agenda.png\"\r\n"
             f"Content-Type: image/png\r\n\r\n").encode()
    body += open(png2, "rb").read() + f"\r\n--{boundary}--\r\n".encode()
    st, res = api(f"{WEBHOOK}?wait=true", "POST", body,
                  {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    if st == 200 and res.get("id"):
        json.dump({"id": res["id"], "posted": str(today)}, open(STATE, "w"))
        print(f"✅ 投稿OK: message_id={res['id']}")
    else:
        raise SystemExit(f"投稿失敗: status={st}")

if __name__ == "__main__":
    main()
