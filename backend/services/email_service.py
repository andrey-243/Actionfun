import os
from datetime import date
import resend

SENTIMENT_EMOJI = {"BULLISH": "📈", "BEARISH": "📉", "NEUTRAL": "➡️"}


def _build_html(digests: list[dict]) -> str:
    sections = []
    for d in digests:
        emoji = SENTIMENT_EMOJI.get(d["sentiment"], "➡️")
        articles_html = "".join(
            f'<li><a href="{a["url"]}" style="color:#4CAF82;text-decoration:none;">{a["headline"]}</a> <span style="color:#9E9B95;font-size:12px;">— {a["source"]}</span></li>'
            for a in d.get("articles", [])[:6]
        )
        sections.append(f"""
<div style="margin-bottom:32px;padding:20px;background:#1E1D1B;border-radius:8px;border:1px solid #302F2C;">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
    <span style="font-family:monospace;font-size:18px;font-weight:800;color:#E8E5DF;">{d['ticker']}</span>
    <span style="padding:2px 10px;border-radius:4px;font-size:11px;font-weight:700;font-family:monospace;
      background:{'#1a3a2a' if d['sentiment']=='BULLISH' else '#3a1a1a' if d['sentiment']=='BEARISH' else '#2a2a2a'};
      color:{'#4CAF82' if d['sentiment']=='BULLISH' else '#E05C4B' if d['sentiment']=='BEARISH' else '#9E9B95'};">
      {emoji} {d['sentiment']}
    </span>
  </div>
  <p style="color:#E8E5DF;font-size:14px;line-height:1.7;margin:0 0 16px;">{d['summary']}</p>
  <ul style="color:#C8C5BF;font-size:13px;line-height:1.8;padding-left:18px;margin:0;">{articles_html}</ul>
</div>""")

    return f"""<!DOCTYPE html>
<html>
<body style="background:#141412;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:32px 16px;">
  <div style="max-width:600px;margin:0 auto;">
    <div style="margin-bottom:28px;">
      <span style="font-family:monospace;font-weight:800;font-size:20px;color:#E8E5DF;">StockVibe</span>
      <span style="margin-left:8px;font-size:12px;color:#9E9B95;">Digest du {date.today().strftime("%d/%m/%Y")}</span>
    </div>
    {''.join(sections)}
    <p style="color:#5C5A56;font-size:11px;margin-top:24px;">
      Tu reçois ce digest car tu suis ces actions sur StockVibe.<br>
      <a href="#" style="color:#5C5A56;">Se désabonner</a>
    </p>
  </div>
</body>
</html>"""


def send_digest_email(to_email: str, digests: list[dict]) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        return

    resend.api_key = api_key
    tickers = ", ".join(d["ticker"] for d in digests)

    resend.Emails.send({
        "from": os.environ.get("RESEND_FROM_EMAIL", "digest@stockvibe.app"),
        "to": [to_email],
        "subject": f"StockVibe · Digest {date.today().strftime('%d/%m/%Y')} — {tickers}",
        "html": _build_html(digests),
    })
