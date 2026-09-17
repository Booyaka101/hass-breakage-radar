"""Turning a rendered blog post into the sentence a rule can quote.

The board shows these messages verbatim, so anything the page wraps around
the prose -- the navigation sidebar, a heading anchor, a nested list -- is
what a user reads unless it is stripped here.
"""

from __future__ import annotations

from tools.blog_rules import _post_body, _text, extract_removals

URL = "https://developers.home-assistant.io/blog/2026/08/31/deprecate-widget-helper/"

# Trimmed from a real Docusaurus page: the chrome, a heading anchor, a
# sentence the source wraps mid-line, and a nested list.
POST = """<!doctype html>
<html><head><title>Deprecating the widget helper | Home Assistant Developer Docs</title>
<style>.navbar { color: red }</style>
<script>window.docusaurus = 1</script></head>
<body>
<nav class="navbar"><a class="skipToContent" href="#main">Skip to main content</a>
<div class="navbar__items">Developers Home Assistant Overview Core Frontend</div></nav>
<main><article class="">
<header><h2><a href="/blog/2026/08/31/deprecate-widget-helper">Deprecating the widget
helper</a><a class="hash-link" href="#deprecating">\u200b</a></h2></header>
<div class="markdown">
<p>The widget helper has been deprecated and will be
removed in Home Assistant 2027.10.</p>
<ul>
<li>ha-widget is the successor of ha-thing
<ul><li>ha-thing API stays but will stop working in 2027.4 .</li></ul>
</li>
</ul>
</div></article></main>
<footer>Copyright Home Assistant</footer>
</body></html>
"""


def test_a_sentence_the_page_wrapped_survives_as_one_sentence():
    assert (
        "The widget helper has been deprecated and will be removed in "
        "Home Assistant 2027.10." in _text(POST).split("\n")
    )


def test_a_nested_list_item_does_not_run_into_the_one_above_it():
    lines = _text(POST).split("\n")
    assert "ha-widget is the successor of ha-thing" in lines
    assert "ha-thing API stays but will stop working in 2027.4." in lines


def test_the_navigation_is_not_part_of_the_prose():
    first = _text(POST).split("\n")[0]
    assert first == "Deprecating the widget helper | Home Assistant Developer Docs"


def test_every_release_in_the_post_becomes_a_rule_quoting_its_own_sentence():
    rules = {rule["breaks_in"]: rule for rule in extract_removals(URL, _text(POST))}
    assert sorted(rules) == ["2027.10", "2027.4"]
    assert rules["2027.10"]["message"] == (
        "The widget helper has been deprecated and will be removed in "
        "Home Assistant 2027.10."
    )
    # The source writes "2027.4 ." because the version is a link.
    assert rules["2027.4"]["message"] == (
        "ha-thing API stays but will stop working in 2027.4."
    )


def test_no_message_carries_the_page_furniture():
    for rule in extract_removals(URL, _text(POST)):
        assert "Skip to main content" not in rule["message"]
        assert "\u200b" not in rule["message"]
        assert not rule["message"].endswith("...")


#: The same page with another post named in the sidebar, which is where the
#: blog puts the four most recent ones. It renders before the post.
LISTED = POST.replace(
    '<main><article class="">',
    '<aside><nav aria-label="Blog recent posts navigation"><ul>'
    '<li><a href="/blog/2026/09/02/drop-legacy-api">The legacy API will be '
    "removed in 2027.2</a></li></ul></nav></aside>"
    '<main><article class="">',
)


def test_a_post_listed_in_the_sidebar_is_not_quoted_as_this_post():
    """Every page lists the recent posts, and the list comes first, so a
    removal named in one of those titles would be attributed to every post
    crawled while it is up there."""
    rules = extract_removals(URL, _text(_post_body(LISTED)))
    assert [rule["breaks_in"] for rule in rules] == ["2027.10", "2027.4"]
    assert not any("legacy API" in rule["message"] for rule in rules)


def test_the_post_is_the_longest_article_on_the_page():
    """Today's pages carry one. A redesign that wraps each listed post in an
    article of its own would put the chrome back in, reading from the first
    one to the last."""
    listed = POST.replace(
        '<main><article class="">',
        '<article class="teaser"><h2>The legacy API will be removed in '
        "2027.2</h2></article>"
        '<main><article class="">',
    )
    assert listed != POST
    rules = extract_removals(URL, _text(_post_body(listed)))
    assert [rule["breaks_in"] for rule in rules] == ["2027.10", "2027.4"]


def test_two_removals_wrapped_into_one_paragraph_are_both_kept():
    """A hard-wrapped list of removals renders as one paragraph with <br>
    between the lines, and a <br> is not a sentence end. Taking the first
    release named in the sentence drops the rest of the list."""
    post = POST.replace(
        "<p>The widget helper has been deprecated and will be\nremoved in Home Assistant 2027.10.</p>",
        "<p>Deprecated helpers:<br />the widget helper will be removed in "
        "2027.10<br />the gadget helper will be removed in 2027.11.</p>",
    )
    assert post != POST
    releases = [r["breaks_in"] for r in extract_removals(URL, _text(_post_body(post)))]
    assert "2027.10" in releases and "2027.11" in releases


def test_a_page_without_the_element_is_read_whole():
    """A Docusaurus redesign should cost the chrome fix, not every rule."""
    assert _post_body("<html><body><p>removed in 2027.10</p></body></html>").startswith(
        "<html>"
    )


def test_a_sentence_broken_over_a_line_break_is_quoted_whole():
    """Markdown renders a hard-wrapped line as <br>, mid-sentence. Read as a
    block boundary, the rule quotes the page from the break onwards."""
    post = POST.replace(
        "<p>The widget helper has been deprecated and will be\nremoved in Home Assistant 2027.10.</p>",
        "<p>The widget helper has been deprecated<br />and will be removed in "
        "Home Assistant 2027.10.</p>",
    )
    assert post != POST
    rules = extract_removals(URL, _text(_post_body(post)))
    message = next(r["message"] for r in rules if r["breaks_in"] == "2027.10")
    assert message.startswith("The widget helper has been deprecated and will be")


def test_a_sentence_that_names_one_deadline_twice_makes_one_rule():
    """"Supported until 2027.4 and removed in 2027.5" is one removal said two
    ways. Taking both hands the board a deadline a release too early."""
    rules = extract_removals(
        "https://www.home-assistant.io/blog/2027/01/01/thing",
        "The old API is supported until 2027.4 and will be removed in "
        "Home Assistant Core 2027.5.",
    )
    assert [rule["breaks_in"] for rule in rules] == ["2027.5"]


def test_a_schedule_in_bullets_is_still_one_deadline():
    """The support window and the removal are one deadline whether the post
    says both in a sentence or gives each its own bullet, and every block is
    its own sentence since the chrome fix."""
    rules = extract_removals(
        URL,
        _text(
            "<article><p>The shims are deprecated.</p><ul>"
            "<li>Supported until Home Assistant Core 2027.4</li>"
            "<li>Removed in Home Assistant Core 2027.5</li></ul></article>"
        ),
    )
    assert [rule["breaks_in"] for rule in rules] == ["2027.5"]


def test_a_support_window_the_post_backs_up_keeps_its_wording():
    """The developer blog opens with the policy, "deprecated functionality
    remains supported until 2027.8", and names the removals below it. The
    release is real, and that sentence says more in a Repairs card than "It
    is removed in Home Assistant Core 2027.8." does."""
    rules = extract_removals(
        URL,
        _text(
            "<article><p>Unless noted otherwise, deprecated functionality "
            "remains supported until Home Assistant Core 2027.8.</p>"
            "<p>It is removed in Home Assistant Core 2027.8.</p></article>"
        ),
    )
    assert [rule["breaks_in"] for rule in rules] == ["2027.8"]
    assert rules[0]["message"].startswith("Unless noted otherwise")


def test_a_post_that_only_gives_a_support_window_still_makes_a_rule():
    """Plenty of posts never use the word removed. The window is the only
    deadline they give, and dropping it leaves the reader nothing."""
    rules = extract_removals(
        URL,
        _text(
            "<article><p>The old helper is deprecated.</p>"
            "<p>It remains supported until Home Assistant Core 2027.6.</p></article>"
        ),
    )
    assert [rule["breaks_in"] for rule in rules] == ["2027.6"]


def test_two_deprecations_in_one_post_keep_their_own_deadlines():
    """A window read against everything the post says anywhere loses one
    deprecation's deadline to another's removal for landing a release later,
    which is a coincidence, not the same date said twice."""
    rules = extract_removals(
        URL,
        _text(
            "<article><p>Feature X is deprecated and remains supported until "
            "Home Assistant Core 2027.4.</p>"
            "<p>That is the first of the two changes in this post.</p>"
            "<p>Feature Y has its own schedule.</p>"
            "<p>Feature Y is removed in Home Assistant Core 2027.5.</p></article>"
        ),
    )
    assert [rule["breaks_in"] for rule in rules] == ["2027.4", "2027.5"]


def test_two_removal_phrasings_in_one_sentence_both_count():
    """Only the support window is read as the same deadline said twice. Two
    removals in one sentence, phrased differently, are two deadlines."""
    rules = extract_removals(
        "https://www.home-assistant.io/blog/2027/01/01/thing",
        "The widget helper will be removed in 2027.5. The gadget helper, "
        "removed in 2027.5, will stop working in 2027.9 for existing installs.",
    )
    assert sorted(rule["breaks_in"] for rule in rules) == ["2027.5", "2027.9"]
