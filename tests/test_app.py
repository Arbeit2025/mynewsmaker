import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["MYNEWSMAKER_DB"] = _tmp.name

import app as app_module  # noqa: E402


def make_form(**overrides):
    form = {
        "masthead": "ココア新聞",
        "issue_date": "2026-11-02",
        "headline": ["朝の紅茶が完成", "ミニココ通信"],
        "body": ["今朝の紅茶は香り高く仕上がった。", "ミニココが台所に集合した。"],
    }
    form.update(overrides)
    return form


class MyNewsMakerTest(unittest.TestCase):
    def setUp(self):
        app_module.init_db()
        self.client = app_module.app.test_client()
        with app_module.app.app_context():
            conn = app_module.get_db()
            conn.execute("DELETE FROM articles")
            conn.execute("DELETE FROM issues")
            conn.commit()

    def issue_count(self):
        with app_module.app.app_context():
            conn = app_module.get_db()
            return conn.execute("SELECT COUNT(*) AS c FROM issues").fetchone()["c"]

    def test_healthz(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["status"], "ok")

    def test_index_shows_form(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("マイ新聞メーカー", body)
        self.assertIn("新しい号を作る", body)
        self.assertIn("まだ号がありません", body)

    def test_create_and_list(self):
        res = self.client.post("/issues", data=make_form())
        self.assertEqual(res.status_code, 302)
        self.assertEqual(self.issue_count(), 1)
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("ココア新聞", body)
        self.assertIn("記事2件", body)

    def test_view_shows_headlines_and_bodies(self):
        res = self.client.post("/issues", data=make_form())
        view = self.client.get(res.headers["Location"]).get_data(as_text=True)
        self.assertIn("ココア新聞", view)          # 題字
        self.assertIn("2026年11月2日", view)        # 日付（日本語表記）
        self.assertIn("朝の紅茶が完成", view)        # 記事1の見出し
        self.assertIn("今朝の紅茶は香り高く仕上がった。", view)  # 記事1の本文
        self.assertIn("ミニココ通信", view)          # 記事2の見出し
        self.assertIn("ミニココが台所に集合した。", view)        # 記事2の本文

    def test_create_issue_function(self):
        with app_module.app.app_context():
            db = app_module.get_db()
            iid = app_module.create_issue(
                db, "テスト新聞", "2026-01-01",
                [{"headline": "見出しA", "body": "本文A"}, {"headline": "見出しB", "body": ""}],
            )
            issue, articles = app_module.fetch_issue(db, iid)
        self.assertEqual(issue["masthead"], "テスト新聞")
        self.assertEqual([a["headline"] for a in articles], ["見出しA", "見出しB"])

    def test_delete_issue(self):
        res = self.client.post("/issues", data=make_form())
        location = res.headers["Location"]
        iid = int(location.rstrip("/").split("/")[-1])
        res = self.client.post(f"/issues/{iid}/delete")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(self.issue_count(), 0)
        self.assertEqual(self.client.get(location).status_code, 404)
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("まだ号がありません", body)

    def test_view_missing_issue_404(self):
        self.assertEqual(self.client.get("/issues/9999").status_code, 404)

    def test_validation_empty_headline_article(self):
        form = make_form(headline=["見出しあり", ""], body=["本文あり", "見出しなしの本文"])
        res = self.client.post("/issues", data=form)
        self.assertEqual(res.status_code, 400)
        self.assertIn("見出しが空の記事があります", res.get_data(as_text=True))
        self.assertEqual(self.issue_count(), 0)

    def test_validation_no_articles(self):
        form = make_form(headline=["", ""], body=["", ""])
        res = self.client.post("/issues", data=form)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.issue_count(), 0)

    def test_validation_empty_masthead(self):
        res = self.client.post("/issues", data=make_form(masthead=""))
        self.assertEqual(res.status_code, 400)
        self.assertIn("新聞名", res.get_data(as_text=True))
        self.assertEqual(self.issue_count(), 0)

    def test_validation_bad_date(self):
        res = self.client.post("/issues", data=make_form(issue_date="2026/11/02"))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.issue_count(), 0)

    def test_date_defaults_to_today(self):
        form = make_form()
        del form["issue_date"]
        res = self.client.post("/issues", data=form)
        self.assertEqual(res.status_code, 302)
        view = self.client.get(res.headers["Location"]).get_data(as_text=True)
        self.assertIn(app_module.format_date_jp(app_module.today_str()), view)

    def test_blank_article_slots_ignored(self):
        form = make_form(headline=["見出しのみ有効", "", ""], body=["本文", "", ""])
        res = self.client.post("/issues", data=form)
        self.assertEqual(res.status_code, 302)
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("記事1件", body)

    def test_format_date_jp(self):
        self.assertEqual(app_module.format_date_jp("2026-11-02"), "2026年11月2日")
        self.assertEqual(app_module.format_date_jp(""), "")


if __name__ == "__main__":
    unittest.main()
