<?xml version="1.0" encoding="UTF-8"?>
<!--
  Browsers show a feed as raw XML, which reads as broken to anyone who clicks
  the link rather than subscribing. Readers ignore this stylesheet, so the XML
  contract is unchanged; it only exists for the person who arrives in a browser.
-->
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:output method="html" encoding="UTF-8" indent="yes"/>

  <xsl:template match="/rss/channel">
    <html lang="en">
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title><xsl:value-of select="title"/></title>
        <style>
:root {
  --bg:#0f1216; --panel:#171c22; --line:#262d36; --ink:#e6edf3;
  --muted:#8b98a5; --accent:#38bdf8; --warn:#f59e0b;
}
@media (prefers-color-scheme: light) {
  :root { --bg:#f6f8fa; --panel:#fff; --line:#d8dee4; --ink:#1f2328;
          --muted:#57606a; --accent:#0969da; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
header { padding:32px 20px 12px; max-width:900px; margin:0 auto; }
h1 { margin:0 0 6px; font-size:28px; letter-spacing:-.02em; }
.sub { color:var(--muted); margin:0 0 18px; max-width:70ch; }
main { max-width:900px; margin:0 auto; padding:0 20px 60px; }
a { color:var(--accent); }
.note { background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:14px 16px; margin:0 0 26px; color:var(--muted); font-size:14px; }
.note code { color:var(--ink); background:rgba(127,127,127,.15);
  padding:1px 5px; border-radius:5px; word-break:break-all; }
article { border-top:1px solid var(--line); padding:22px 0; }
article > h2 { font-size:19px; margin:0 0 2px;
  display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.pill { font-size:12px; font-weight:600; padding:2px 9px; border-radius:999px;
  background:var(--warn); color:#000; }
.when { color:var(--muted); font-size:13px; margin:0 0 10px; }
article h3 { font-size:13px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--muted); margin:18px 0 6px; }
article ul { margin:6px 0 14px; padding-left:18px; font-size:14px; }
article li { margin:5px 0; }
table { width:100%; border-collapse:collapse; background:var(--panel);
  border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:14px; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
tr:last-child td { border-bottom:0; }
footer { color:var(--muted); font-size:13px; padding:0 20px 40px;
  max-width:900px; margin:0 auto; }
        </style>
      </head>
      <body>
        <header>
          <h1><xsl:value-of select="title"/></h1>
          <p class="sub"><xsl:value-of select="description"/></p>
        </header>
        <main>
          <div class="note">
            This is an <strong>RSS feed</strong>, meant for a feed reader rather than
            a browser. Paste this address into yours to follow along:
            <code><xsl:value-of select="atom:link/@href"
              xmlns:atom="http://www.w3.org/2005/Atom"/></code>
            Home Assistant's own <code>feedreader</code> integration takes it too, so
            you can trigger an automation when a removal is announced. Everything
            below is what the feed carries right now.
          </div>
          <xsl:apply-templates select="item"/>
        </main>
        <footer>
          <p>
            <a href="{link}">Breakage Radar board</a>
            &#183; updated <xsl:value-of select="lastBuildDate"/>
          </p>
        </footer>
      </body>
    </html>
  </xsl:template>

  <xsl:template match="item">
    <article>
      <!-- No release pill: the title already names it, and the lead sentence
           below carries the count the board shows there. -->
      <h2><a href="{link}"><xsl:value-of select="title"/></a></h2>
      <p class="when">Announced <xsl:value-of select="pubDate"/></p>
      <!-- The body is generated HTML in a CDATA block, so it goes out as markup. -->
      <xsl:value-of select="description" disable-output-escaping="yes"/>
    </article>
  </xsl:template>
</xsl:stylesheet>
