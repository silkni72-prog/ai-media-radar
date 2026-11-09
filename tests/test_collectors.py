from datetime import datetime

from src.analysis.keywords import prefilter
from src.collectors.html_list import extract_links, parse_detail
from src.collectors.normalize import normalize_url, title_fingerprint, url_hash
from src.collectors.registry import load_sources
from src.collectors.rss import parse_feed
from src.config import ROOT
from src.models import Group, RawArticle, SourceConfig


def src(**kw):
    base = dict(id="t", name="Test", group="ai_global", type="rss", url="https://x/feed", max_items=10)
    base.update(kw)
    return SourceConfig(**base)


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Old item</title><link>https://ex.com/a?utm_source=rss</link><pubDate>Mon, 02 Nov 2026 10:00:00 +0000</pubDate>
<description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>New item</title><link>https://ex.com/b/</link><pubDate>Sun, 08 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>No date</title><link>https://ex.com/c</link></item>
<item><title></title><link>https://ex.com/d</link></item>
</channel></rss>"""

KOREAN_RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>
<item><title>언론사 AI 도입</title><link>https://www.mediatoday.co.kr/news/articleView.html?idxno=123</link>
<pubDate>2026-11-09 09:35:01</pubDate></item></channel></rss>""".encode()


def test_normalize_url_strips_tracking_and_keeps_article_id():
    assert normalize_url("HTTP://Ex.com/a/?utm_source=x&fbclid=y#top") == "https://ex.com/a"
    assert normalize_url("https://www.mediatoday.co.kr/news/articleView.html?idxno=123&utm_medium=rss") == \
        "https://www.mediatoday.co.kr/news/articleView.html?idxno=123"
    assert normalize_url("https://arxiv.org/abs/2611.01234v2") == normalize_url("http://export.arxiv.org/abs/2611.01234v1".replace("export.", ""))
    assert url_hash("https://ex.com/a?utm_campaign=1") == url_hash("https://ex.com/a/")


def test_title_fingerprint_ignores_tags_and_order():
    assert title_fingerprint("[속보] 오픈AI 새 모델 공개") == title_fingerprint("오픈AI 새 모델 공개")
    assert title_fingerprint("OpenAI launches GPT") == title_fingerprint("GPT launches OpenAI")


def test_parse_feed_sorts_limits_and_flags_missing_date():
    items = parse_feed(RSS, src(max_items=2))
    assert [i.title for i in items] == ["New item", "Old item"]  # 최신순, 제목 없는 항목 제외, 2개 제한
    assert items[1].url == "https://ex.com/a" and items[1].summary == "Hello world"
    all_items = parse_feed(RSS, src())
    nodate = [i for i in all_items if i.title == "No date"][0]
    assert nodate.published_at is None and nodate.published_uncertain


def test_korean_naive_date_is_kst():
    items = parse_feed(KOREAN_RSS, src(group="korea"))
    assert items[0].published_at == datetime(2026, 11, 9, 0, 35, 1)  # 09:35 KST = 00:35 UTC


def test_future_date_becomes_uncertain():
    rss = RSS.replace(b"Sun, 08 Nov 2026 10:00:00 GMT", b"Sun, 08 Nov 2099 10:00:00 GMT")
    item = [i for i in parse_feed(rss, src()) if i.title == "New item"][0]
    assert item.published_at is None and item.published_uncertain


def test_html_list_extracts_links_and_detail_meta():
    html = """<a href="/news/claude-new">Claude new <span>Oct 1, 2026</span></a>
    <a href="/news/announcements">Announcements</a><a href="/careers">x</a>
    <a href="https://www.anthropic.com/news/claude-new?x=1#y">dup</a>"""
    links = extract_links(html, "https://www.anthropic.com/news", r"^/news/[a-z0-9][a-z0-9\-]+$", ["/news/announcements"])
    assert links == [("https://www.anthropic.com/news/claude-new", "Claude new Oct 1, 2026")]
    d = parse_detail('<meta property="og:title" content="Big launch"/>'
                     '<meta property="article:published_time" content="2026-10-22T00:00:00.000Z"/>'
                     "<p>" + "This is a sufficiently long first paragraph of the article body text. " * 2 + "</p>")
    assert d["title"] == "Big launch" and d["published_at"] == datetime(2026, 10, 22)
    assert d["summary"].startswith("This is")


def test_prefilter():
    def art(title, summary="", cat="AI"):
        return RawArticle(source_id="s", source_name="s", group=Group.KOREA, url="https://x", title=title,
                          summary=summary, default_category=cat, prefilter=True)
    assert prefilter(art("삼성, 생성형 AI 신제품 공개"))
    assert prefilter(art("오픈AI가 새 모델 발표"))
    assert not prefilter(art("He said the price was paid"))  # 'ai' 오탐 없음
    assert not prefilter(art("부동산 시장 동향"))
    assert prefilter(art("JTBC 매각 공시", cat="MEDIA"))
    assert not prefilter(art("[인사] 매경AX", cat="MEDIA"))


def test_sources_yaml_is_valid_and_unique():
    sources = load_sources(ROOT / "config" / "sources.yaml", include_disabled=True)
    assert len({s.id for s in sources}) == len(sources)
    wired = [s for s in sources if s.group == Group.WIRED and s.enabled]
    assert {s.id for s in wired} == {"wired_ai", "wired_business", "wired_backchannel", "wired_top"}
    for s in sources:
        assert s.verified, f"{s.id}: 접근 확인 메모가 없음"
