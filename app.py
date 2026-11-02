"""マイ新聞メーカー mynewsmaker

メモや出来事を「新聞の一面」風レイアウトで表示するメーカー。
新聞（号）を作成し、題字（新聞名）・日付・複数の記事（見出し＋本文）を
SQLite に保存する。号の一覧と、段組み風の表示ページを持つ。
単一ファイル Flask + SQLite。
"""
import os
import sqlite3
from datetime import date, datetime
from itertools import zip_longest
from zoneinfo import ZoneInfo

from flask import Flask, abort, g, jsonify, redirect, render_template_string, request, url_for

app = Flask(__name__)
JST = ZoneInfo("Asia/Tokyo")
DB_PATH = os.environ.get("MYNEWSMAKER_DB", os.path.join(os.path.dirname(__file__), "mynewsmaker.db"))

MAX_ARTICLES = 20  # 1号あたりの記事の上限


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            masthead TEXT NOT NULL,
            issue_date TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL,
            headline TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    db.commit()
    db.close()


def now_str():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def today_str():
    return datetime.now(JST).strftime("%Y-%m-%d")


def format_date_jp(iso_date):
    """'2026-11-02' を '2026年11月2日' にする。変換できなければそのまま返す。"""
    try:
        d = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return iso_date or ""
    return f"{d.year}年{d.month}月{d.day}日"


def parse_issue_form(form):
    """フォームから号の入力を取り出して検証する。

    戻り値は (masthead, issue_date, articles, error)。
    articles は [{"headline": ..., "body": ...}, ...]（入力順）。
    見出しも本文も空の記事欄は無視する。本文だけあって見出しが空の記事は
    エラーにする。記事が1件も無ければエラー。
    """
    masthead = (form.get("masthead") or "").strip()
    if not masthead:
        return "", "", [], "新聞名（題字）を入力してください。"

    issue_date = (form.get("issue_date") or "").strip()
    if not issue_date:
        issue_date = today_str()
    else:
        try:
            date.fromisoformat(issue_date)
        except ValueError:
            return "", "", [], "日付の形式が正しくありません。"

    headlines = form.getlist("headline")
    bodies = form.getlist("body")
    articles = []
    for headline, body in zip_longest(headlines, bodies, fillvalue=""):
        headline = (headline or "").strip()
        body = (body or "").strip()
        if not headline and not body:
            continue
        if not headline:
            return "", "", [], "見出しが空の記事があります。見出しを入力してください。"
        articles.append({"headline": headline, "body": body})
    if not articles:
        return "", "", [], "記事がありません。見出しと本文を入力してください。"
    if len(articles) > MAX_ARTICLES:
        return "", "", [], f"記事は1号につき{MAX_ARTICLES}件までです。"
    return masthead, issue_date, articles, ""


def create_issue(db, masthead, issue_date, articles):
    cur = db.execute(
        "INSERT INTO issues(masthead, issue_date, created_at) VALUES(?, ?, ?)",
        (masthead, issue_date, now_str()),
    )
    issue_id = cur.lastrowid
    for i, a in enumerate(articles):
        db.execute(
            "INSERT INTO articles(issue_id, headline, body, position) VALUES(?, ?, ?, ?)",
            (issue_id, a["headline"], a["body"], i),
        )
    db.commit()
    return issue_id


def fetch_issue(db, issue_id):
    issue = db.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
    if issue is None:
        return None, []
    articles = db.execute(
        "SELECT * FROM articles WHERE issue_id = ? ORDER BY position, id", (issue_id,)
    ).fetchall()
    return issue, articles


INDEX = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>マイ新聞メーカー</title>
<style>body{font-family:sans-serif;max-width:760px;margin:2em auto;padding:0 1em}
.err{background:#fdecea;border:1px solid #d9534f;padding:.8em;border-radius:6px}
fieldset{margin:.8em 0}textarea{width:100%}input.headline{width:100%}
.note{color:#555;font-size:.9em}</style>
</head><body>
<h1>マイ新聞メーカー</h1>
<p>メモや出来事を「新聞の一面」風に仕立てるメーカーです。新聞名と日付、記事（見出し＋本文）を入れて発行すると、段組み風の紙面で表示されます。</p>

{% if error %}<p class="err">{{ error }}</p>{% endif %}

<h2>新しい号を作る</h2>
<form method="post" action="{{ url_for('create') }}">
  <label>新聞名（題字） <input type="text" name="masthead" required size="30" placeholder="例：ココア新聞" value="{{ form.masthead or '' }}"></label><br>
  <label>日付 <input type="date" name="issue_date" value="{{ form.issue_date or today }}"></label>
  <div id="articles">
  {% for a in form.articles %}
    <fieldset class="article-block"><legend>記事{{ loop.index }}</legend>
      <label>見出し <input type="text" name="headline" class="headline" value="{{ a.headline }}"></label><br>
      <label>本文<br><textarea name="body" rows="4">{{ a.body }}</textarea></label>
    </fieldset>
  {% endfor %}
  </div>
  <button type="button" onclick="addArticle()">記事欄を増やす</button>
  <button type="submit">この内容で発行する</button>
</form>
<script>
function addArticle(){
  var blocks = document.querySelectorAll('.article-block');
  var clone = blocks[blocks.length-1].cloneNode(true);
  clone.querySelector('legend').textContent = '記事' + (blocks.length+1);
  clone.querySelectorAll('input,textarea').forEach(function(el){ el.value=''; });
  document.getElementById('articles').appendChild(clone);
}
</script>

<h2>発行した号の一覧（{{ issues|length }}件）</h2>
<ul>
{% for i in issues %}
<li>{{ i.issue_date }} <a href="{{ url_for('view_issue', iid=i.id) }}">{{ i.masthead }}</a>（記事{{ i.article_count }}件）
  <form method="post" action="{{ url_for('delete_issue', iid=i.id) }}" style="display:inline" onsubmit="return confirm('この号を削除しますか？')"><button>削除</button></form></li>
{% else %}<li>まだ号がありません</li>{% endfor %}
</ul>
<p class="note">保存先は SQLite です。無料プランでは再デプロイ等でデータが消える場合があります。</p>
</body></html>"""

VIEW = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ issue.masthead }}（{{ issue.issue_date }}）- マイ新聞メーカー</title>
<style>
body{font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",serif;background:#f5f1e6;margin:0;padding:1em}
.paper{max-width:900px;margin:0 auto;background:#fffdf5;border:1px solid #999;padding:1.5em 2em;box-shadow:0 2px 8px rgba(0,0,0,.15)}
.masthead{font-size:3em;font-weight:bold;text-align:center;letter-spacing:.2em;border-top:4px double #222;border-bottom:4px double #222;padding:.2em 0;margin:.1em 0}
.dateline{display:flex;justify-content:space-between;border-bottom:2px solid #222;padding:.3em 0;font-size:.95em}
.columns{column-count:2;column-gap:2em;column-rule:1px solid #bbb;margin-top:1em}
@media (max-width:640px){.columns{column-count:1}.masthead{font-size:2em}}
article{break-inside:avoid;margin-bottom:1.2em}
article h2{font-size:1.3em;border-bottom:1px solid #222;padding-bottom:.15em;margin:.2em 0 .4em}
article.lead h2{font-size:1.8em}
article p{line-height:1.9;text-align:justify;white-space:pre-wrap;margin:0}
nav{font-family:sans-serif}
</style>
</head><body>
<nav><a href="{{ url_for('index') }}">一覧に戻る</a></nav>
<div class="paper">
  <div class="masthead">{{ issue.masthead }}</div>
  <div class="dateline"><span>{{ date_jp }}</span><span>第{{ issue.id }}号</span><span>マイ新聞メーカー</span></div>
  <div class="columns">
  {% for a in articles %}
    <article{% if loop.first %} class="lead"{% endif %}>
      <h2>{{ a.headline }}</h2>
      <p>{{ a.body }}</p>
    </article>
  {% endfor %}
  </div>
</div>
<form method="post" action="{{ url_for('delete_issue', iid=issue.id) }}" onsubmit="return confirm('この号を削除しますか？')" style="margin-top:1em">
  <button>この号を削除する</button>
</form>
</body></html>"""


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    db = get_db()
    issues = db.execute(
        "SELECT i.*, (SELECT COUNT(*) FROM articles a WHERE a.issue_id = i.id) AS article_count"
        " FROM issues i ORDER BY i.issue_date DESC, i.id DESC"
    ).fetchall()
    form = {"masthead": "", "issue_date": "", "articles": [{"headline": "", "body": ""} for _ in range(3)]}
    return render_template_string(INDEX, issues=issues, form=form, today=today_str(), error="")


@app.route("/issues", methods=["POST"])
def create():
    db = get_db()
    masthead, issue_date, articles, error = parse_issue_form(request.form)
    if error:
        issues = db.execute(
            "SELECT i.*, (SELECT COUNT(*) FROM articles a WHERE a.issue_id = i.id) AS article_count"
            " FROM issues i ORDER BY i.issue_date DESC, i.id DESC"
        ).fetchall()
        form = {
            "masthead": (request.form.get("masthead") or "").strip(),
            "issue_date": (request.form.get("issue_date") or "").strip(),
            "articles": articles or [{"headline": "", "body": ""}],
        }
        return render_template_string(INDEX, issues=issues, form=form, today=today_str(), error=error), 400
    issue_id = create_issue(db, masthead, issue_date, articles)
    return redirect(url_for("view_issue", iid=issue_id))


@app.route("/issues/<int:iid>")
def view_issue(iid):
    db = get_db()
    issue, articles = fetch_issue(db, iid)
    if issue is None:
        abort(404)
    return render_template_string(VIEW, issue=issue, articles=articles, date_jp=format_date_jp(issue["issue_date"]))


@app.route("/issues/<int:iid>/delete", methods=["POST"])
def delete_issue(iid):
    db = get_db()
    db.execute("DELETE FROM articles WHERE issue_id = ?", (iid,))
    db.execute("DELETE FROM issues WHERE id = ?", (iid,))
    db.commit()
    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
